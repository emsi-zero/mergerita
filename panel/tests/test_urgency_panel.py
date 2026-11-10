from django.contrib.auth import get_user_model
from django.test import TestCase

from audit.models import AuditEvent
from integrations.models import OutboxCall
from panel.tests.helpers import panel_login
from panel.tests.test_merge_requests import create_merge_request, create_person
from reviews.models import (
    MergeRequestState,
    UrgencyRequest,
    UrgencyRequestStatus,
)

TAB_URL = "/panel/urgency/"
APPROVE_URL = "/panel/urgency/{}/approve/"
DECLINE_URL = "/panel/urgency/{}/decline/"


class UrgencyTabTests(TestCase):
    def setUp(self) -> None:
        self.boss = create_person(1, "Bob Boss", "boss")
        self.author = create_person(2, "Ann Author", "ann")
        self.admin = create_person(3, "Ada Admin", "ada")
        self.admin.is_admin = True
        self.admin.save()
        self.bystander = create_person(4, "Sam Bystander", "sam")
        self.merge_request = create_merge_request(
            self.author, 5, "Card retries", MergeRequestState.IN_REVIEW
        )
        self.pending = UrgencyRequest.objects.create(
            merge_request=self.merge_request,
            requester=self.author,
            approver=self.boss,
            reason="Release blocker",
        )

    def assert_unchanged(self) -> None:
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, UrgencyRequestStatus.PENDING)
        self.merge_request.refresh_from_db()
        self.assertIsNone(self.merge_request.urgent_at)
        self.assertFalse(OutboxCall.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())

    def test_urg_9_approver_and_admin_decide_from_the_tab(self):
        """URG-9: Given a pending request, when its approver or an admin opens
        the tab, then they see it and can approve or decline with a note; the tab
        link appears only for a person who may decide or an admin."""
        # given
        panel_login(self.client, self.boss.user)

        # when
        response = self.client.get(TAB_URL)

        # then
        self.assertContains(response, "Release blocker")
        self.assertContains(response, 'href="/panel/urgency/"')
        with self.subTest("an admin sees it too"):
            panel_login(self.client, self.admin.user)

            self.assertContains(self.client.get(TAB_URL), "Release blocker")
        with self.subTest("a bystander sees no request and no tab"):
            panel_login(self.client, self.bystander.user)
            response = self.client.get(TAB_URL)

            self.assertNotContains(response, "Release blocker")
            self.assertNotContains(response, 'href="/panel/urgency/"')
        with self.subTest("the approver approves with a note"):
            panel_login(self.client, self.boss.user)

            self.client.post(APPROVE_URL.format(self.pending.pk), {"note": "Go"})

            self.pending.refresh_from_db()
            self.assertEqual(self.pending.status, UrgencyRequestStatus.APPROVED)
            self.assertEqual(self.pending.note, "Go")
            self.merge_request.refresh_from_db()
            self.assertIsNotNone(self.merge_request.urgent_at)
        with self.subTest("an admin declines another request"):
            other = create_merge_request(
                self.author, 6, "Other", MergeRequestState.IN_REVIEW
            )
            second = UrgencyRequest.objects.create(
                merge_request=other, requester=self.author, reason="Soon"
            )
            panel_login(self.client, self.admin.user)

            self.client.post(DECLINE_URL.format(second.pk), {"note": "No"})

            second.refresh_from_db()
            self.assertEqual(second.status, UrgencyRequestStatus.DECLINED)

    def test_urg_10_a_person_who_may_not_decide_gets_404_and_nothing_changes(self):
        """URG-10: Given a pending request, when an anonymous visitor, an
        unlinked user, the author, a non-approver non-admin, or the approver of a
        decided request tries to decide it, then the answer is 404 (or a sign-in
        redirect) and nothing changes."""
        # given
        self.author.is_admin = True
        self.author.save()
        unlinked = get_user_model().objects.create_user("nobody", password="pw")
        decided = UrgencyRequest.objects.create(
            merge_request=create_merge_request(
                self.author, 6, "Done", MergeRequestState.IN_REVIEW
            ),
            requester=self.author,
            approver=self.boss,
            reason="Old",
            status=UrgencyRequestStatus.DECLINED,
        )
        cases = [
            ("anonymous", None, self.pending.pk, 302),
            ("unlinked user", unlinked, self.pending.pk, 403),
            ("the author, even as admin", self.author.user, self.pending.pk, 404),
            ("neither approver nor admin", self.bystander.user, self.pending.pk, 404),
            ("a missing request", self.boss.user, 9999, 404),
        ]

        for label, user, pk, status in cases:
            for url in (APPROVE_URL, DECLINE_URL):
                with self.subTest(label, url=url):
                    self.client.logout()
                    self.client.cookies.clear()
                    if user is not None:
                        panel_login(self.client, user)

                    # when
                    response = self.client.post(url.format(pk), {"note": "x"})

                    # then
                    self.assertEqual(response.status_code, status)
                    self.assert_unchanged()
                    decided.refresh_from_db()
                    self.assertEqual(decided.status, UrgencyRequestStatus.DECLINED)
