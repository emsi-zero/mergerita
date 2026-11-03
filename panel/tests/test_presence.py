import threading
from datetime import timedelta

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from audit.models import AuditEvent
from integrations.models import OutboxCall
from panel.tests.helpers import panel_login
from panel.tests.test_merge_requests import create_merge_request, create_person
from panel.tests.test_reviews import book
from reviews import presence as presence_service
from reviews.models import (
    AreaReview,
    CodeArea,
    MergeRequestState,
    PresenceRequest,
    PresenceRequestState,
)

REQUEST_URL = "/panel/reviews/{}/presence/"
CANCEL_URL = "/panel/reviews/{}/presence/cancel/"


class PresenceTests(TestCase):
    def setUp(self) -> None:
        self.me = create_person(1, "Rita Reviewer", "rita")
        self.other = create_person(2, "Other", "other")
        self.author = create_person(3, "Author Ann", "ann")
        self.area = CodeArea.objects.create(name="payments", size_limit_lines=300)
        self.merge_request = create_merge_request(
            self.author, 5, "Card retries", MergeRequestState.IN_REVIEW
        )
        self.day = timezone.localdate()
        self.review = book(self.me, self.merge_request, self.area, self.day, "owner")
        panel_login(self.client, self.me.user)

    def notifications(self) -> list[dict]:
        return [call.payload for call in OutboxCall.objects.filter(kind="notify")]

    def test_mrv_4_request_stores_notifies_author_and_audits(self):
        """MRV-4: Given a review booked to me, when I request the author's
        presence with a note, then an active request is stored, one chat message
        names the MR, me, the day and the note, and one audit event is written.
        """
        response = self.client.post(
            REQUEST_URL.format(self.review.pk), {"note": "Please explain the retry"}
        )

        self.assertRedirects(response, "/panel/reviews/", fetch_redirect_response=False)
        presence = PresenceRequest.objects.get()
        self.assertEqual(presence.area_review, self.review)
        self.assertEqual(presence.requested_by, self.me)
        self.assertEqual(presence.state, PresenceRequestState.ACTIVE)
        self.assertEqual(presence.note, "Please explain the retry")
        self.assertEqual(
            self.notifications(),
            [
                {
                    "gitlab_user_ids": [3],
                    "text": (
                        "Rita Reviewer asks you to join the review of "
                        f"!5 Card retries on {self.day.isoformat()}. "
                        "Please explain the retry"
                    ),
                }
            ],
        )
        audit = AuditEvent.objects.get(action="presence.requested")
        self.assertEqual(audit.actor, "user:rita")
        self.assertEqual(audit.subject_id, str(presence.pk))

    def test_mrv_5_active_request_blocks_a_second_one_and_shows_cancel(self):
        """MRV-5: Given a review with an active request, when I request again,
        then nothing is stored or sent, and My reviews shows "Author requested"
        with "Cancel request" instead of the request button.
        """
        self.client.post(REQUEST_URL.format(self.review.pk), {"note": "first"})

        self.client.post(REQUEST_URL.format(self.review.pk), {"note": "second"})

        self.assertEqual(PresenceRequest.objects.count(), 1)
        self.assertEqual(len(self.notifications()), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action="presence.requested").count(), 1
        )
        page = self.client.get("/panel/reviews/")
        self.assertContains(page, "Author requested")
        self.assertContains(page, "Cancel request")
        self.assertNotContains(page, "Request author")

    def test_mrv_6_cancel_withdraws_notifies_author_and_audits(self):
        """MRV-6: Given an active request, when I cancel it, then it is
        withdrawn, one chat message tells the author they are no longer needed,
        and one audit event is written.
        """
        self.client.post(REQUEST_URL.format(self.review.pk), {"note": ""})

        response = self.client.post(CANCEL_URL.format(self.review.pk))

        self.assertRedirects(response, "/panel/reviews/", fetch_redirect_response=False)
        presence = PresenceRequest.objects.get()
        self.assertEqual(presence.state, PresenceRequestState.WITHDRAWN)
        self.assertIsNotNone(presence.withdrawn_at)
        self.assertEqual(
            self.notifications()[1],
            {
                "gitlab_user_ids": [3],
                "text": (
                    "Rita Reviewer no longer needs you at the review of "
                    f"!5 on {self.day.isoformat()}."
                ),
            },
        )
        self.assertEqual(len(self.notifications()), 2)
        self.assertEqual(
            AuditEvent.objects.filter(action="presence.withdrawn").count(), 1
        )
        with self.subTest("a second cancel sends nothing"):
            self.client.post(CANCEL_URL.format(self.review.pk))

            self.assertEqual(len(self.notifications()), 2)

    @override_settings(RMO_PRESENCE_NOTE_MAX_LENGTH=500)
    def test_mrv_7_foreign_review_or_long_note_is_refused_and_nothing_is_stored(self):
        """MRV-7: Given a review that is not booked to me, or a note over the
        limit, when I request or cancel, then the reply is 404 or 400 and
        nothing is stored or sent.
        """
        theirs = book(self.other, self.merge_request, self.area, self.day, "theirs")
        unbooked = AreaReview.objects.create(merge_request=self.merge_request)
        for name, url in (
            ("request foreign", REQUEST_URL.format(theirs.pk)),
            ("cancel foreign", CANCEL_URL.format(theirs.pk)),
            ("request unbooked", REQUEST_URL.format(unbooked.pk)),
            ("request missing", REQUEST_URL.format(99999)),
        ):
            with self.subTest(name):
                response = self.client.post(url, {"note": "x"})

                self.assertEqual(response.status_code, 404)
        with self.subTest("note too long"):
            response = self.client.post(
                REQUEST_URL.format(self.review.pk), {"note": "x" * 501}
            )

            self.assertEqual(response.status_code, 400)
        with self.subTest("nothing stored or sent"):
            self.assertEqual(PresenceRequest.objects.count(), 0)
            self.assertEqual(self.notifications(), [])
            self.assertEqual(
                AuditEvent.objects.filter(action__startswith="presence.").count(), 0
            )

    def test_mrv_8_author_sees_active_requests_on_the_mr_page(self):
        """MRV-8: Given an active and a withdrawn request on my MR, when I open
        the MR, then I see the active one with the reviewer, area, day and note,
        and not the withdrawn one.
        """
        PresenceRequest.objects.create(
            area_review=self.review,
            requested_by=self.me,
            note="Join at the start",
            state=PresenceRequestState.WITHDRAWN,
        )
        PresenceRequest.objects.create(
            area_review=self.review, requested_by=self.me, note="Join me please"
        )
        panel_login(self.client, self.author.user)

        response = self.client.get(f"/panel/mrs/{self.merge_request.pk}/")

        self.assertContains(response, "Rita Reviewer")
        self.assertContains(response, "payments")
        self.assertContains(response, self.day.strftime("%a"))
        self.assertContains(response, "Join me please", count=1)
        self.assertNotContains(response, "Join at the start")
        with self.subTest("a request for a past day is not shown"):
            past = book(
                self.me,
                self.merge_request,
                self.area,
                self.day - timedelta(days=1),
                "past",
            )
            PresenceRequest.objects.create(
                area_review=past, requested_by=self.me, note="Join yesterday"
            )

            response = self.client.get(f"/panel/mrs/{self.merge_request.pk}/")

            self.assertNotContains(response, "Join yesterday")
            self.assertContains(response, "Join me please", count=1)


class PresenceConcurrencyTests(TransactionTestCase):
    def test_concurrent_requests_store_one_request_and_send_one_message(self):
        """Concurrency (MRV 5): Given one booked review, when two threads request
        the author's presence at the same time, then one active request and one
        chat message exist.
        """
        reviewer = create_person(1, "Rita Reviewer", "rita")
        author = create_person(3, "Author Ann", "ann")
        area = CodeArea.objects.create(name="payments", size_limit_lines=300)
        merge_request = create_merge_request(
            author, 5, "Card retries", MergeRequestState.IN_REVIEW
        )
        review = book(reviewer, merge_request, area, timezone.localdate(), "owner")
        barrier = threading.Barrier(2)
        results: list[object] = []

        def request() -> None:
            barrier.wait()
            try:
                results.append(
                    presence_service.request_presence(
                        reviewer, review.pk, "note", "user:rita"
                    )
                )
            except Exception as error:  # pylint: disable=broad-exception-caught
                results.append(error)
            finally:
                connection.close()

        threads = [threading.Thread(target=request) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # One call stores the request, the other sees it and does nothing. Without
        # the row lock the second would raise IntegrityError instead of returning.
        self.assertCountEqual(results, [True, False])
        self.assertEqual(
            PresenceRequest.objects.filter(state=PresenceRequestState.ACTIVE).count(), 1
        )
        self.assertEqual(OutboxCall.objects.filter(kind="notify").count(), 1)
