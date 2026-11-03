from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from audit.models import AuditEvent
from integrations.models import OutboxCall
from panel.tests.helpers import panel_login
from panel.tests.test_merge_requests import create_merge_request, create_person
from panel.tests.test_reviews import book
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CapacityDay,
    CodeArea,
    Leave,
    MergeRequestState,
    OwnerRole,
    Ownership,
    PresenceRequest,
    PresenceRequestState,
    ReviewSession,
    ReviewSessionOutcome,
)

DONE_URL = "/panel/sessions/{}/done/"
MORE_TIME_URL = "/panel/sessions/{}/more-time/"
HAND_BACK_URL = "/panel/sessions/{}/hand-back/"
EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]


class OutcomeTests(TestCase):  # pylint: disable=too-many-instance-attributes
    def setUp(self) -> None:
        self.me = create_person(1, "Rita Reviewer", "rita")
        self.backup = create_person(2, "Bob Backup", "bob")
        self.author = create_person(3, "Author Ann", "ann")
        admin = create_person(4, "Admin", "admin")
        admin.is_admin = True
        admin.save()
        for person in (self.me, self.backup, self.author):
            person.working_days = EVERY_DAY
            person.save()
        self.area = CodeArea.objects.create(name="payments", size_limit_lines=300)
        Ownership.objects.create(person=self.me, area=self.area, role=OwnerRole.PRIMARY)
        Ownership.objects.create(
            person=self.backup, area=self.area, role=OwnerRole.BACKUP
        )
        self.today = timezone.localdate()
        self.merge_request = create_merge_request(
            self.author, 5, "Card retries", MergeRequestState.IN_REVIEW
        )
        self.review, self.session = self.book_session(
            self.merge_request, self.today, ReviewSessionOutcome.PENDING
        )
        CapacityDay.objects.create(
            person=self.me, day=self.today, budget_minutes=120, booked_minutes=60
        )
        panel_login(self.client, self.me.user)

    def book_session(
        self, merge_request, day, outcome: str, area: CodeArea | None = None
    ) -> tuple[AreaReview, ReviewSession]:
        review = book(self.me, merge_request, area or self.area, day, "primary owner")
        session = ReviewSession.objects.create(
            area_review=review,
            reviewer=self.me,
            day=day,
            slot_minutes=60,
            reason="primary owner",
            outcome=outcome,
        )
        return review, session

    def notifications(self) -> list[dict]:
        return [call.payload for call in OutboxCall.objects.filter(kind="notify")]

    def test_rvo_2_done_closes_the_session_and_leaves_approval_alone(self):
        """RVO-2: Given my pending session, when I mark it Done, then its outcome
        is done, an audit event is written, and the area review and MR keep
        their state.
        """
        response = self.client.post(DONE_URL.format(self.session.pk))

        self.assertRedirects(response, "/panel/reviews/", fetch_redirect_response=False)
        self.session.refresh_from_db()
        self.review.refresh_from_db()
        self.merge_request.refresh_from_db()
        self.assertEqual(self.session.outcome, ReviewSessionOutcome.DONE)
        self.assertIsNotNone(self.session.outcome_at)
        self.assertEqual(self.review.state, AreaReviewState.BOOKED)
        self.assertEqual(self.merge_request.state, MergeRequestState.IN_REVIEW)
        audit = AuditEvent.objects.get(action="review_session.done")
        self.assertEqual(audit.actor, "user:rita")
        self.assertEqual(audit.subject_id, str(self.session.pk))

    def test_rvo_3_more_time_books_the_same_reviewer_on_the_next_fitting_day(
        self,
    ):  # pylint: disable=too-many-locals
        """RVO-3: Given my pending session, when I ask for more time, then it is
        closed as more_time and a continuation session is booked with me on the
        next day with free budget and I am notified; and when no day fits, the
        area review becomes unbooked and an admin alert is queued.
        """
        with self.subTest(name="next_fitting_day"):
            # given
            tomorrow = self.today + timedelta(days=1)
            CapacityDay.objects.create(
                person=self.me, day=tomorrow, budget_minutes=120, booked_minutes=120
            )
            day_after = tomorrow + timedelta(days=1)

            # when
            self.client.post(MORE_TIME_URL.format(self.session.pk))

            # then
            self.session.refresh_from_db()
            self.review.refresh_from_db()
            new = ReviewSession.objects.get(outcome=ReviewSessionOutcome.PENDING)
            self.assertEqual(self.session.outcome, ReviewSessionOutcome.MORE_TIME)
            self.assertEqual(new.reviewer, self.me)
            self.assertEqual(new.day, day_after)
            self.assertTrue(new.is_continuation)
            self.assertEqual(self.review.booked_day, day_after)
            self.assertEqual(
                self.notifications(),
                [
                    {
                        "gitlab_user_ids": [1],
                        "text": f"Next session for !5 booked on {day_after.isoformat()}.",
                    }
                ],
            )
            self.assertFalse(OutboxCall.objects.filter(kind="set_reviewer").exists())

        with self.subTest(name="nothing_fits"):
            # given
            other_mr = create_merge_request(
                self.author, 6, "Other", MergeRequestState.IN_REVIEW
            )
            review, session = self.book_session(
                other_mr, self.today, ReviewSessionOutcome.PENDING
            )
            Leave.objects.create(
                person=self.me,
                start_date=self.today + timedelta(days=1),
                end_date=self.today + timedelta(days=60),
            )

            # when
            self.client.post(MORE_TIME_URL.format(session.pk))

            # then
            review.refresh_from_db()
            self.assertEqual(review.state, AreaReviewState.UNBOOKED)
            self.assertTrue(
                AuditEvent.objects.filter(
                    action="area_review.unbooked", subject_id=str(review.pk)
                ).exists()
            )
            self.assertFalse(
                ReviewSession.objects.filter(
                    area_review=review, outcome=ReviewSessionOutcome.PENDING
                ).exists()
            )
            self.assertEqual(self.notifications()[-1]["gitlab_user_ids"], [4])

        with self.subTest(name="withdraws_presence_request"):
            # given
            asked_mr = create_merge_request(
                self.author, 12, "Asked", MergeRequestState.IN_REVIEW
            )
            asked, asked_session = self.book_session(
                asked_mr, self.today, ReviewSessionOutcome.PENDING
            )
            ask = PresenceRequest.objects.create(
                area_review=asked, requested_by=self.me
            )

            # when
            self.client.post(MORE_TIME_URL.format(asked_session.pk))

            # then
            ask.refresh_from_db()
            self.assertEqual(ask.state, PresenceRequestState.WITHDRAWN)
            self.assertTrue(
                AuditEvent.objects.filter(action="presence.withdrawn").exists()
            )
            self.assertIn(
                {
                    "gitlab_user_ids": [3],
                    "text": "Rita Reviewer no longer needs you at the review of "
                    f"!12 on {self.today.isoformat()}.",
                },
                self.notifications(),
            )

    def test_rvo_4_hand_back_books_another_owner_and_gives_minutes_back(  # pylint: disable=too-many-locals,too-many-statements
        self,
    ):
        """RVO-4: Given my pending session, when I hand it back, then it is
        closed as handed_back, a continuation session is booked with another
        owner who is set in GitLab and notified, my minutes of a day from today
        on are given back, and other area reviews stay as they are; and when no
        other owner fits, the area review becomes unbooked and an admin alert
        is queued.
        """
        with self.subTest(name="another_owner"):
            # given
            sibling, sibling_session = self.book_session(
                self.merge_request, self.today, ReviewSessionOutcome.PENDING
            )

            # when
            self.client.post(HAND_BACK_URL.format(self.session.pk))

            # then
            self.session.refresh_from_db()
            self.review.refresh_from_db()
            sibling.refresh_from_db()
            sibling_session.refresh_from_db()
            new = ReviewSession.objects.get(
                area_review=self.review, outcome=ReviewSessionOutcome.PENDING
            )
            self.assertEqual(self.session.outcome, ReviewSessionOutcome.HANDED_BACK)
            self.assertEqual(new.reviewer, self.backup)
            self.assertTrue(new.is_continuation)
            self.assertEqual(self.review.reviewer, self.backup)
            self.assertEqual(
                CapacityDay.objects.get(person=self.me, day=self.today).booked_minutes,
                0,
            )
            self.assertEqual(
                OutboxCall.objects.get(kind="set_reviewer").payload[
                    "reviewer_gitlab_user_id"
                ],
                2,
            )
            self.assertEqual(self.notifications()[0]["gitlab_user_ids"], [2])
            self.assertEqual(sibling.reviewer, self.me)
            self.assertEqual(sibling_session.outcome, ReviewSessionOutcome.PENDING)

        with self.subTest(name="past_day_keeps_minutes"):
            # given
            yesterday = self.today - timedelta(days=1)
            past_mr = create_merge_request(
                self.author, 7, "Past", MergeRequestState.IN_REVIEW
            )
            _, past_session = self.book_session(
                past_mr, yesterday, ReviewSessionOutcome.PENDING
            )
            CapacityDay.objects.create(
                person=self.me, day=yesterday, budget_minutes=120, booked_minutes=60
            )

            # when
            self.client.post(HAND_BACK_URL.format(past_session.pk))

            # then
            self.assertEqual(
                CapacityDay.objects.get(person=self.me, day=yesterday).booked_minutes,
                60,
            )

        with self.subTest(name="no_other_owner_fits"):
            # given
            Leave.objects.create(
                person=self.backup,
                start_date=self.today,
                end_date=self.today + timedelta(days=60),
            )
            lone_mr = create_merge_request(
                self.author, 8, "Lone", MergeRequestState.IN_REVIEW
            )
            lone, lone_session = self.book_session(
                lone_mr, self.today, ReviewSessionOutcome.PENDING
            )

            # when
            self.client.post(HAND_BACK_URL.format(lone_session.pk))

            # then
            lone.refresh_from_db()
            self.assertEqual(lone.state, AreaReviewState.UNBOOKED)
            self.assertEqual(self.notifications()[-1]["gitlab_user_ids"], [4])

        with self.subTest(name="withdraws_presence_request"):
            # given
            asked_mr = create_merge_request(
                self.author, 14, "Asked back", MergeRequestState.IN_REVIEW
            )
            asked, asked_session = self.book_session(
                asked_mr, self.today, ReviewSessionOutcome.PENDING
            )
            ask = PresenceRequest.objects.create(
                area_review=asked, requested_by=self.me
            )

            # when
            self.client.post(HAND_BACK_URL.format(asked_session.pk))

            # then
            ask.refresh_from_db()
            self.assertEqual(ask.state, PresenceRequestState.WITHDRAWN)
            self.assertTrue(
                AuditEvent.objects.filter(action="presence.withdrawn").exists()
            )
            self.assertIn(
                {
                    "gitlab_user_ids": [3],
                    "text": "Rita Reviewer no longer needs you at the review of "
                    f"!14 on {self.today.isoformat()}.",
                },
                self.notifications(),
            )

        with self.subTest(name="never_books_the_author"):
            # given
            shared = CodeArea.objects.create(name="ledger", size_limit_lines=300)
            Ownership.objects.create(
                person=self.me, area=shared, role=OwnerRole.PRIMARY
            )
            Ownership.objects.create(
                person=self.author, area=shared, role=OwnerRole.BACKUP
            )
            own_mr = create_merge_request(
                self.author, 13, "Own", MergeRequestState.IN_REVIEW
            )
            only, only_session = self.book_session(
                own_mr, self.today, ReviewSessionOutcome.PENDING, shared
            )

            # when
            self.client.post(HAND_BACK_URL.format(only_session.pk))

            # then
            only.refresh_from_db()
            self.assertEqual(only.state, AreaReviewState.UNBOOKED)
            self.assertFalse(
                ReviewSession.objects.filter(reviewer=self.author).exists()
            )
            self.assertEqual(self.notifications()[-1]["gitlab_user_ids"], [4])

    def test_rvo_5_outcome_of_a_session_that_is_not_mine_and_pending_is_404(self):
        """RVO-5: Given a session of another reviewer, a session that has an
        outcome, and a session of an unbooked area review, when I send Done,
        Need more time or Hand back for it, then the reply is 404 and nothing
        changes; and a second click on the same outcome is 404 too.
        """
        theirs = create_merge_request(
            self.author, 9, "Theirs", MergeRequestState.IN_REVIEW
        )
        their_review = book(self.backup, theirs, self.area, self.today, "x")
        their_session = ReviewSession.objects.create(
            area_review=their_review,
            reviewer=self.backup,
            day=self.today,
            slot_minutes=60,
        )
        closed_mr = create_merge_request(
            self.author, 10, "Closed", MergeRequestState.IN_REVIEW
        )
        _, closed = self.book_session(closed_mr, self.today, ReviewSessionOutcome.DONE)
        unbooked_mr = create_merge_request(
            self.author, 11, "Unbooked", MergeRequestState.IN_REVIEW
        )
        unbooked, unbooked_session = self.book_session(
            unbooked_mr, self.today, ReviewSessionOutcome.PENDING
        )
        unbooked.state = AreaReviewState.UNBOOKED
        unbooked.save()
        for name, session in (
            ("other_reviewer", their_session),
            ("has_outcome", closed),
            ("unbooked_area_review", unbooked_session),
        ):
            for url in (DONE_URL, MORE_TIME_URL, HAND_BACK_URL):
                with self.subTest(case=name, url=url):
                    response = self.client.post(url.format(session.pk))

                    self.assertEqual(response.status_code, 404)
        with self.subTest(case="double_click"):
            self.client.post(MORE_TIME_URL.format(self.session.pk))
            sessions_after_first = ReviewSession.objects.count()

            response = self.client.post(MORE_TIME_URL.format(self.session.pk))

            self.assertEqual(response.status_code, 404)
            self.assertEqual(ReviewSession.objects.count(), sessions_after_first)
        with self.subTest(case="nothing_changed"):
            their_session.refresh_from_db()
            self.assertEqual(their_session.outcome, ReviewSessionOutcome.PENDING)
