from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import TestCase

from panel.tests.helpers import panel_login
from panel.tests.test_merge_requests import create_merge_request, create_person
from reviews.models import (
    AreaReview,
    CodeArea,
    MergeAssignment,
    MergeRequestState,
)

TEHRAN = ZoneInfo("Asia/Tehran")


class MyMergesTest(TestCase):
    """My merges panel page."""

    def test_mrg_4_merge_owner_sees_open_assignments_in_window_order(self) -> None:
        """MRG-4: Given a merge owner with open, cancelled, merged and foreign
        assignments, when they open My merges, then only their open ones show
        in window order with areas, approvers and a Go to MR link, and a person
        who is not a merge owner gets 404 and no tab."""
        # given
        owner = create_person(1, "Owner", "owner")
        owner.is_merge_owner = True
        owner.save()
        stranger = create_person(2, "Stranger", "stranger")
        author = create_person(3, "Author", "author")
        reviewer = create_person(4, "Reviewer", "reviewer")
        area = CodeArea.objects.create(
            name="billing", path_patterns=[], size_limit_lines=9
        )

        def assign(iid: int, hour: int, merge_owner=owner, **fields) -> None:
            merge_request = create_merge_request(
                author, iid, f"Title {iid}", MergeRequestState.APPROVED
            )
            merge_request.web_url = f"https://gitlab.example/mr/{iid}"
            merge_request.save()
            area_review = AreaReview.objects.create(
                merge_request=merge_request, reviewer=reviewer, state="approved"
            )
            area_review.areas.set([area])
            MergeAssignment.objects.create(
                merge_request=merge_request,
                merge_owner=merge_owner,
                window=datetime(2026, 11, 4, hour, 0, tzinfo=TEHRAN),
                **fields,
            )

        assign(1, 15)
        assign(2, 10)
        assign(3, 11, cancelled_at=datetime(2026, 11, 4, 9, 0, tzinfo=TEHRAN))
        assign(4, 12, merged_at=datetime(2026, 11, 4, 9, 0, tzinfo=TEHRAN))
        assign(5, 13, merge_owner=stranger)
        panel_login(self.client, owner.user)

        # when
        response = self.client.get("/panel/merges/")
        tab_on_other_page = self.client.get("/panel/mrs/")

        # then
        self.assertEqual(
            [a.merge_request.iid for a in response.context["assignments"]], [2, 1]
        )
        self.assertContains(response, "Go to MR", count=2)
        self.assertContains(response, "billing")
        self.assertContains(response, "Reviewer")
        self.assertContains(response, "Wed 4 Nov, 10:00")
        self.assertContains(tab_on_other_page, 'href="/panel/merges/"')
        with self.subTest("not a merge owner"):
            panel_login(self.client, stranger.user)

            self.assertEqual(self.client.get("/panel/merges/").status_code, 404)
            self.assertNotContains(self.client.get("/panel/mrs/"), "My merges")
