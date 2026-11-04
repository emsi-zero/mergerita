from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from audit.models import AuditEvent
from panel.tests.helpers import panel_login
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CodeArea,
    MergeRequest,
    MergeRequestState,
    Person,
)


def create_person(gitlab_user_id: int, name: str, username: str) -> Person:
    user = get_user_model().objects.create_user(username, password="pw")
    return Person.objects.create(gitlab_user_id=gitlab_user_id, name=name, user=user)


def create_merge_request(
    author: Person, iid: int, title: str, state: str, changed_lines: int = 10
) -> MergeRequest:
    return MergeRequest.objects.create(
        project_id=7,
        iid=iid,
        author=author,
        title=title,
        state=state,
        changed_lines=changed_lines,
    )


class MergeRequestPanelTests(TestCase):
    def setUp(self) -> None:
        self.me = create_person(1, "Me", "me")
        self.other = create_person(2, "Other", "other")
        panel_login(self.client, self.me.user)

    def test_dvp_3_list_shows_only_own_merge_requests(self):
        """DVP-3: Given MRs of two authors,
        when I open My MRs, then I see only mine with title, state label and summary.
        """
        create_merge_request(
            self.me, 1, "My waiting MR", MergeRequestState.WAITING_ON_AUTHOR
        )
        create_merge_request(
            self.me, 2, "My review MR", MergeRequestState.AWAITING_AI_REVIEW
        )
        create_merge_request(
            self.other, 3, "Their secret MR", MergeRequestState.IN_REVIEW
        )

        response = self.client.get("/panel/mrs/")

        self.assertContains(response, "My waiting MR")
        self.assertContains(response, "Your move")
        self.assertContains(response, "AI Reviewer requested changes")
        self.assertContains(response, "My review MR")
        self.assertContains(response, "Waiting for the AI Reviewer")
        self.assertNotContains(response, "Their secret MR")

    def test_dvp_4_detail_shows_reviews_next_step_history_and_hides_others(self):
        """DVP-4: Given my booked MR,
        when I open it, then I see reviews, next step and history; another's MR is 404.
        """
        reviewer = create_person(3, "Sara Reviewer", "sara")
        area = CodeArea.objects.create(
            name="payments", path_patterns=["p/*"], size_limit_lines=500
        )
        mine = create_merge_request(self.me, 1, "Mine", MergeRequestState.IN_REVIEW)
        area_review = AreaReview.objects.create(
            merge_request=mine,
            state=AreaReviewState.BOOKED,
            reviewer=reviewer,
            booked_day=date(2026, 10, 29),
            reason="primary owner; 120 of 120 minutes free",
        )
        area_review.areas.set([area])
        AuditEvent.objects.create(
            actor="gitlab",
            action="mr.opened",
            subject_type="reviews.MergeRequest",
            subject_id=str(mine.pk),
        )
        AuditEvent.objects.create(
            actor="system",
            action="area_review.booked",
            subject_type="reviews.AreaReview",
            subject_id=str(area_review.pk),
        )
        AuditEvent.objects.create(
            actor="system",
            action="mr.opened",
            subject_type="reviews.MergeRequest",
            subject_id=str(mine.pk + 1000),
        )
        theirs = create_merge_request(
            self.other, 2, "Theirs", MergeRequestState.IN_REVIEW
        )

        their_review = AreaReview.objects.create(
            merge_request=theirs, reason="DECOY-REASON"
        )
        AuditEvent.objects.create(
            actor="system",
            action="area_review.created",
            subject_type="reviews.AreaReview",
            subject_id=str(their_review.pk),
            after={"note": "DECOY-HISTORY"},
        )

        response = self.client.get(f"/panel/mrs/{mine.pk}/")
        other_response = self.client.get(f"/panel/mrs/{theirs.pk}/")

        self.assertContains(response, "payments")
        self.assertContains(response, "Sara Reviewer")
        self.assertContains(response, "Thu 29 Oct")
        self.assertContains(response, "primary owner; 120 of 120 minutes free")
        self.assertContains(response, "Nothing to do")
        self.assertContains(response, "Opened")
        self.assertContains(response, "Reviewer booked")
        self.assertEqual(len(response.context["history"]), 2)
        self.assertNotContains(response, "DECOY")
        self.assertNotContains(response, "Area review created")
        self.assertEqual(other_response.status_code, 404)

    def test_history_labels_a_merge_outside_its_window_or_without_an_owner(self):
        """Given my merged MRs, when I open them, then the history says whether
        the merge was outside its window, by another person than the merge
        owner, or had no merge owner assigned."""
        for iid, name, flags, label in (
            (1, "clean", (False, False, 5), "Merged"),
            (2, "outside", (True, False, 5), "Merged (outside its window)"),
            (
                3,
                "other",
                (False, True, 5),
                "Merged (not by the assigned merge owner)",
            ),
            (4, "no_owner", (False, False, None), "Merged (no merge owner assigned)"),
        ):
            with self.subTest(name):
                # given
                merged = create_merge_request(
                    self.me, iid, name, MergeRequestState.MERGED
                )
                AuditEvent.objects.create(
                    actor="gitlab",
                    action="mr.merged",
                    subject_type="reviews.MergeRequest",
                    subject_id=str(merged.pk),
                    after={
                        "outside_window": flags[0],
                        "by_other_person": flags[1],
                        "merge_owner": flags[2],
                    },
                )

                # when
                response = self.client.get(f"/panel/mrs/{merged.pk}/")

                # then
                self.assertEqual(
                    [text for _, text in response.context["history"]], [label]
                )

    def test_dvp_5_detail_warns_when_size_exceeds_an_area_limit(self):
        """DVP-5: Given an MR over an area's limit,
        when I open it, then a size warning shows; at the limit it does not.
        """
        area = CodeArea.objects.create(
            name="api", path_patterns=["api/*"], size_limit_lines=400
        )
        for changed_lines, expect_warning in ((401, True), (400, False)):
            with self.subTest(changed_lines=changed_lines):
                merge_request = create_merge_request(
                    self.me,
                    changed_lines,
                    "Sized",
                    MergeRequestState.AWAITING_BOOKING,
                    changed_lines,
                )
                AreaReview.objects.create(merge_request=merge_request).areas.set([area])

                response = self.client.get(f"/panel/mrs/{merge_request.pk}/")

                self.assertEqual(
                    response.context["size_warning"] is not None, expect_warning
                )
                if expect_warning:
                    self.assertContains(response, "Over the 400-line limit of the api")


class MergeRequestPageLayoutTests(TestCase):
    """My MRs as one page: the list with the selected MR's detail beside it."""

    def setUp(self) -> None:
        self.me = create_person(1, "Me", "me")
        self.other = create_person(2, "Other", "other")
        panel_login(self.client, self.me.user)

    def test_pns_1_my_mrs_shows_the_list_with_the_selected_detail(self):
        """PNS-1: Given my MRs (one merged, one waiting on me) and another
        developer's MR, when I open My MRs, then the list shows mine with the
        most recent one's detail beside it and the head counts open MRs and
        those that need my action; with an MR id in the URL, that one is
        selected; another developer's MR id is 404; with no MRs, no detail."""
        # given
        now = timezone.now()
        older = create_merge_request(
            self.me, 1, "Older waiting MR", MergeRequestState.WAITING_ON_AUTHOR
        )
        merged = create_merge_request(self.me, 2, "Merged MR", MergeRequestState.MERGED)
        newest = create_merge_request(
            self.me, 3, "Newest review MR", MergeRequestState.IN_REVIEW
        )
        for merge_request, hours_ago in ((older, 3), (merged, 2), (newest, 1)):
            merge_request.opened_at = now - timedelta(hours=hours_ago)
            merge_request.save()
        theirs = create_merge_request(
            self.other, 4, "Their MR", MergeRequestState.IN_REVIEW
        )

        # when / then
        with self.subTest("default selection"):
            response = self.client.get("/panel/mrs/")

            self.assertEqual(response.context["merge_request"], newest)
            self.assertContains(response, "2 open · 1 need your action")
            for title in ("Older waiting MR", "Merged MR", "Newest review MR"):
                self.assertContains(response, title)
            self.assertNotContains(response, "Their MR")
            self.assertContains(
                response, f'href="/panel/mrs/{newest.pk}/" aria-current="page"'
            )
        with self.subTest("selected in the URL"):
            response = self.client.get(f"/panel/mrs/{older.pk}/")

            self.assertEqual(response.context["merge_request"], older)
            self.assertContains(response, "Newest review MR")
            self.assertContains(
                response, f'href="/panel/mrs/{older.pk}/" aria-current="page"'
            )
            self.assertContains(response, "Merge request detail")
        with self.subTest("another developer's MR"):
            response = self.client.get(f"/panel/mrs/{theirs.pk}/")

            self.assertEqual(response.status_code, 404)
        with self.subTest("no MRs"):
            panel_login(self.client, create_person(5, "New", "new").user)

            response = self.client.get("/panel/mrs/")

            self.assertContains(response, "You have no merge requests.")
            self.assertNotContains(response, "Merge request detail")

    def test_pns_3_detail_links_to_gitlab_and_shows_the_project(self):
        """PNS-3: Given my MR with its GitLab address, when I open it, then the
        detail has "Open in GitLab" in a new tab and the project path; without
        the address, neither shows."""
        # given
        with_url = create_merge_request(
            self.me, 1, "Linked", MergeRequestState.IN_REVIEW
        )
        with_url.web_url = "https://gitlab.example/shop/backend/-/merge_requests/1"
        with_url.save()
        without_url = create_merge_request(
            self.me, 2, "Unlinked", MergeRequestState.IN_REVIEW
        )

        # when
        linked = self.client.get(f"/panel/mrs/{with_url.pk}/")
        unlinked = self.client.get(f"/panel/mrs/{without_url.pk}/")

        # then
        self.assertContains(
            linked,
            '<a class="btn btn-primary" '
            'href="https://gitlab.example/shop/backend/-/merge_requests/1" '
            'target="_blank" rel="noopener noreferrer">Open in GitLab</a>',
            html=True,
        )
        self.assertContains(linked, "shop/backend · Opened")
        self.assertNotContains(unlinked, "Open in GitLab")
        self.assertContains(unlinked, '<div class="muted detail-meta">Opened')
