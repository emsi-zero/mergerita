# pylint: disable=duplicate-code

import threading
import time as time_module
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import connection, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from audit.models import AuditEvent
from integrations.models import OutboxCall
from panel.tests.helpers import panel_login
from panel.tests.test_merge_requests import create_merge_request, create_person
from reviews import urgency
from reviews.models import (
    Leave,
    MergeRequest,
    MergeRequestState,
    Person,
    Team,
    UrgencyRequest,
    UrgencyRequestStatus,
)

REQUEST_URL = "/panel/mrs/{}/urgency/"
WITHDRAW_URL = "/panel/mrs/{}/urgency/withdraw/"
TAB_URL = "/panel/urgency/"
APPROVE_URL = "/panel/urgency/{}/approve/"


def texts() -> list[str]:
    return [
        call.payload["text"]
        for call in OutboxCall.objects.filter(kind="notify").order_by("pk")
    ]


def recipients() -> list[list[int]]:
    return [
        call.payload["gitlab_user_ids"]
        for call in OutboxCall.objects.filter(kind="notify").order_by("pk")
    ]


def decided_request(author: Person, iid: int, local_naive: datetime) -> UrgencyRequest:
    """An approved request decided at a local wall-clock time."""
    return UrgencyRequest.objects.create(
        merge_request=create_merge_request(
            author, iid, f"MR {iid}", MergeRequestState.IN_REVIEW
        ),
        requester=author,
        reason="Old",
        status=UrgencyRequestStatus.APPROVED,
        decided_at=timezone.make_aware(local_naive),
    )


def panel_login_client(user) -> Client:
    client = Client()
    panel_login(client, user)
    return client


def leave_today(person: Person) -> None:
    today = timezone.localdate()
    Leave.objects.create(person=person, start_date=today, end_date=today)


def pending_request(
    merge_request: MergeRequest, requester: Person, approver: Person | None
) -> UrgencyRequest:
    return UrgencyRequest.objects.create(
        merge_request=merge_request,
        requester=requester,
        approver=approver,
        reason="Release blocker",
    )


def wait_until_blocked_on_locks(count: int) -> None:
    """Return once `count` connections wait on a row lock (or fail after 5 s)."""
    deadline = time_module.monotonic() + 5
    while time_module.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE wait_event_type = 'Lock' AND datname = current_database()"
            )
            if cursor.fetchone()[0] >= count:
                return
        time_module.sleep(0.02)
    raise AssertionError(f"fewer than {count} connections waited on the lock")


class RequestTests(TestCase):
    def setUp(self) -> None:
        self.boss = create_person(1, "Bob Boss", "boss")
        self.author = create_person(2, "Ann Author", "ann")
        self.author.reports_to = self.boss
        self.author.save()
        self.admin = create_person(3, "Ada Admin", "ada")
        self.admin.is_admin = True
        self.admin.save()
        self.merge_request = create_merge_request(
            self.author, 5, "Card retries", MergeRequestState.IN_REVIEW
        )

    def test_urg_5_author_requests_urgency_for_an_open_mr(self):
        """URG-5: Given the author of an open MR, when they submit a reason on
        My MRs, then one pending request is stored for the approver, the approver
        is notified and urgency.requested is written; another state, another
        author, or an empty or over-long reason is refused with a message."""
        # given
        panel_login(self.client, self.author.user)
        refused = [
            ("empty reason", self.merge_request.pk, ""),
            ("long reason", self.merge_request.pk, "x" * 501),
            (
                "merged",
                create_merge_request(
                    self.author, 6, "Old", MergeRequestState.MERGED
                ).pk,
                "Why",
            ),
            (
                "waiting on author",
                create_merge_request(
                    self.author, 7, "Fix", MergeRequestState.WAITING_ON_AUTHOR
                ).pk,
                "Why",
            ),
            (
                "someone else's",
                create_merge_request(
                    self.boss, 8, "Boss MR", MergeRequestState.IN_REVIEW
                ).pk,
                "Why",
            ),
        ]

        # when
        for label, pk, reason in refused:
            with self.subTest(label):
                response = self.client.post(REQUEST_URL.format(pk), {"reason": reason})

                # then
                self.assertEqual(response.status_code, 302)
                self.assertFalse(UrgencyRequest.objects.exists())
                self.assertFalse(OutboxCall.objects.exists())
        other = create_person(9, "Olga Other", "olga")
        decoy = pending_request(
            create_merge_request(other, 20, "Decoy", MergeRequestState.IN_REVIEW),
            other,
            self.boss,
        )
        with self.subTest("another author posts this MR"):
            panel_login(self.client, other.user)

            self.client.post(
                REQUEST_URL.format(self.merge_request.pk), {"reason": "Mine now"}
            )

            self.assertEqual(list(UrgencyRequest.objects.all()), [decoy])
            self.assertEqual(decoy.status, UrgencyRequestStatus.PENDING)
        with self.subTest("anonymous"):
            response = Client().post(
                REQUEST_URL.format(self.merge_request.pk), {"reason": "Now"}
            )

            self.assertEqual(response.status_code, 302)
            self.assertEqual(list(UrgencyRequest.objects.all()), [decoy])
        with self.subTest("a user without a Person"):
            stranger = get_user_model().objects.create_user("nobody", password="pw")
            client = Client()
            panel_login(client, stranger)

            response = client.post(
                REQUEST_URL.format(self.merge_request.pk), {"reason": "Now"}
            )

            self.assertEqual(response.status_code, 403)
            self.assertEqual(list(UrgencyRequest.objects.all()), [decoy])
        UrgencyRequest.objects.all().delete()
        OutboxCall.objects.all().delete()
        panel_login(self.client, self.author.user)
        response = self.client.post(
            REQUEST_URL.format(self.merge_request.pk),
            {"reason": "Release blocker"},
            follow=True,
        )
        stored = UrgencyRequest.objects.get()
        self.assertEqual(
            (stored.merge_request, stored.requester, stored.approver, stored.status),
            (
                self.merge_request,
                self.author,
                self.boss,
                UrgencyRequestStatus.PENDING,
            ),
        )
        self.assertEqual(
            texts(),
            [
                "Urgency requested for MR 7:5 by Ann Author: Release blocker. "
                "Decide in the panel."
            ],
        )
        self.assertEqual(recipients(), [[1]])
        self.assertEqual(
            AuditEvent.objects.filter(action="urgency.requested").count(), 1
        )
        with self.subTest("a second request is refused"):
            self.client.post(
                REQUEST_URL.format(self.merge_request.pk), {"reason": "Again"}
            )

            self.assertEqual(UrgencyRequest.objects.count(), 1)
            self.assertEqual(len(texts()), 1)
        self.assertEqual(response.status_code, 200)
        with self.subTest("an author nobody approves: the admins"):
            self.author.reports_to = None
            no_boss = create_merge_request(
                self.author, 9, "Top", MergeRequestState.IN_REVIEW
            )

            urgency.request(self.author, no_boss.pk, "Now", "user:ann")

            self.assertIsNone(
                UrgencyRequest.objects.get(merge_request=no_boss).approver
            )
            self.assertEqual(recipients()[-1], [3])

    def test_urg_7_author_withdraws_a_pending_request(self):
        """URG-7: Given a pending request, when the author withdraws it, then
        it is withdrawn, the approver is notified and urgency.withdrawn is
        written; a decided request cannot be withdrawn."""
        # given
        panel_login(self.client, self.author.user)
        pending = pending_request(self.merge_request, self.author, self.boss)

        # when
        self.client.post(WITHDRAW_URL.format(self.merge_request.pk))

        # then
        pending.refresh_from_db()
        self.assertEqual(pending.status, UrgencyRequestStatus.WITHDRAWN)
        self.assertEqual(texts(), ["The urgency request for MR 7:5 was withdrawn."])
        self.assertEqual(recipients(), [[1]])
        self.assertEqual(
            AuditEvent.objects.filter(action="urgency.withdrawn").count(), 1
        )
        with self.subTest("a decided request: a message"):
            decided = pending_request(self.merge_request, self.author, self.boss)
            UrgencyRequest.objects.filter(pk=decided.pk).update(
                status=UrgencyRequestStatus.APPROVED
            )

            response = self.client.post(WITHDRAW_URL.format(self.merge_request.pk))

            self.assertRedirects(
                response,
                f"/panel/mrs/{self.merge_request.pk}/",
                fetch_redirect_response=False,
            )
            self.assertEqual(
                str(list(get_messages(response.wsgi_request))[-1]),
                "This request is no longer pending.",
            )
            decided.refresh_from_db()
            self.assertEqual(decided.status, UrgencyRequestStatus.APPROVED)
            self.assertEqual(len(texts()), 1)
        with self.subTest("another author, anonymous, unlinked user"):
            UrgencyRequest.objects.all().delete()
            mine = pending_request(self.merge_request, self.author, self.boss)
            other = create_person(9, "Olga Other", "olga")
            decoy = pending_request(
                create_merge_request(other, 20, "Decoy", MergeRequestState.IN_REVIEW),
                other,
                self.boss,
            )
            stranger = get_user_model().objects.create_user("nobody", password="pw")
            unlinked = Client()
            panel_login(unlinked, stranger)
            outcomes = [
                ("another author", panel_login_client(other.user), 404),
                ("anonymous", Client(), 302),
                ("unlinked", unlinked, 403),
            ]
            for label, client, status in outcomes:
                with self.subTest(label):
                    response = client.post(WITHDRAW_URL.format(self.merge_request.pk))

                    self.assertEqual(response.status_code, status)
                    mine.refresh_from_db()
                    decoy.refresh_from_db()
                    self.assertEqual(mine.status, UrgencyRequestStatus.PENDING)
                    self.assertEqual(decoy.status, UrgencyRequestStatus.PENDING)

    def test_urg_8_approver_follows_the_reporting_line(self):
        """URG-8: Given reporting lines, leave and delegates, when the approver
        is resolved, then it is the manager, else the delegate, else one level
        up, else nobody; never the author, and a loop ends."""
        today = timezone.localdate()
        director = create_person(10, "Dina Director", "dina")
        delegate = create_person(11, "Dan Delegate", "dan")
        team = Team.objects.create(name="Web", lead=self.boss, delegate=delegate)
        self.author.team = team
        self.author.save()
        self.boss.reports_to = director
        self.boss.save()

        with self.subTest("the manager"):
            self.assertEqual(urgency.resolve_approver(self.author, today), self.boss)
        leave_today(self.boss)
        with self.subTest("the manager is on leave: the delegate"):
            self.assertEqual(urgency.resolve_approver(self.author, today), delegate)
        leave_today(delegate)
        with self.subTest("the delegate is on leave too: one level up"):
            self.assertEqual(urgency.resolve_approver(self.author, today), director)
        leave_today(director)
        with self.subTest("nobody is left: the admins"):
            self.assertIsNone(urgency.resolve_approver(self.author, today))
        with self.subTest("the delegate is the author"):
            team.delegate = self.author
            team.save()

            self.assertIsNone(urgency.resolve_approver(self.author, today))
        with self.subTest("a loop back to the author"):
            team.delegate = None
            team.save()
            Person.objects.filter(pk=self.boss.pk).update(reports_to=self.author)
            self.author.refresh_from_db()

            self.assertIsNone(urgency.resolve_approver(self.author, today))

    def test_urg_11_decline_notifies_the_author_and_changes_no_booking(self):
        """URG-11: Given a pending request, when the approver declines with a
        note, then it is declined with the note, the author is notified,
        urgency.declined is written and the MR is not urgent."""
        # given
        pending = pending_request(self.merge_request, self.author, self.boss)

        # when
        urgency.decline(self.boss, pending.pk, "Not now", "user:boss")

        # then
        pending.refresh_from_db()
        self.assertEqual(
            (pending.status, pending.note, pending.decided_by),
            (UrgencyRequestStatus.DECLINED, "Not now", self.boss),
        )
        self.assertEqual(
            texts(), ["Your urgency request for MR 7:5 was declined: Not now"]
        )
        self.assertEqual(recipients(), [[2]])
        self.assertEqual(
            AuditEvent.objects.filter(action="urgency.declined").count(), 1
        )
        self.merge_request.refresh_from_db()
        self.assertIsNone(self.merge_request.urgent_at)

    def test_urg_26_approving_a_request_for_a_closed_mr_is_refused(self):
        """URG-26: Given a pending request whose MR has merged, when the approver
        approves, then it is refused with "This MR is no longer open." and
        nothing changes; declining still works."""
        # given
        team = Team.objects.create(name="Web", lead=self.boss, weekly_urgent_limit=0)
        self.author.team = team
        self.author.save()
        pending = pending_request(self.merge_request, self.author, self.boss)
        MergeRequest.objects.filter(pk=self.merge_request.pk).update(
            state=MergeRequestState.MERGED
        )

        # when
        with self.assertRaisesMessage(
            urgency.UrgencyRefused, "This MR is no longer open."
        ):
            urgency.approve(self.boss, pending.pk, "", "user:boss")

        # then
        pending.refresh_from_db()
        self.assertEqual(pending.status, UrgencyRequestStatus.PENDING)
        self.merge_request.refresh_from_db()
        self.assertIsNone(self.merge_request.urgent_at)
        self.assertFalse(AuditEvent.objects.filter(action="urgency.approved"))
        self.assertFalse(OutboxCall.objects.exists())
        self.assertEqual(urgency.weekly_count(team), 0)
        with self.subTest("declining stays allowed"):
            urgency.decline(self.boss, pending.pk, "Merged", "user:boss")

            pending.refresh_from_db()
            self.assertEqual(pending.status, UrgencyRequestStatus.DECLINED)

    def test_urg_13_team_limit_warns_in_the_tab_and_alerts_on_approval(self):
        """URG-13: Given a team with a weekly limit of 1, when the count reaches
        it, then the tab warns before the decision; an approval over the limit
        still succeeds and alerts the admins once; an old approval is not counted."""
        # given
        team = Team.objects.create(name="Web", lead=self.boss, weekly_urgent_limit=1)
        self.author.team = team
        self.author.save()
        old = create_merge_request(self.author, 6, "Old", MergeRequestState.IN_REVIEW)
        old_request = pending_request(old, self.author, self.boss)
        old_request.status = UrgencyRequestStatus.APPROVED
        old_request.decided_at = timezone.now() - timedelta(days=8)
        old_request.save()
        first = create_merge_request(
            self.author, 7, "First", MergeRequestState.IN_REVIEW
        )
        first_request = pending_request(first, self.author, self.boss)
        panel_login(self.client, self.boss.user)
        with self.subTest("below the limit"):
            response = self.client.get(TAB_URL)

            self.assertNotContains(response, "reached its weekly urgent limit")

        # when
        urgency.approve(self.boss, first_request.pk, "", "user:boss")

        # then
        with self.subTest("the week starts at local midnight six days ago"):
            today = timezone.localdate()
            inside = decided_request(
                self.author,
                20,
                datetime.combine(today - timedelta(days=6), time(0, 0)),
            )
            outside = decided_request(
                self.author,
                21,
                datetime.combine(today - timedelta(days=7), time(23, 59)),
            )

            self.assertEqual(urgency.weekly_count(team), 2)
            inside.delete()
            outside.delete()
        with self.subTest("at the limit: no alert, but a warning"):
            self.assertNotIn("approved urgent requests", " ".join(texts()))
            second = pending_request(self.merge_request, self.author, self.boss)
            response = self.client.get(TAB_URL)
            self.assertContains(response, "reached its weekly urgent limit")
            self.assertContains(response, "1 of 1")
        with self.subTest("over the limit: still approved, one alert"):
            urgency.approve(self.boss, second.pk, "", "user:boss")

            second.refresh_from_db()
            self.assertEqual(second.status, UrgencyRequestStatus.APPROVED)
            alerts = [t for t in texts() if "approved urgent requests" in t]
            self.assertEqual(
                alerts, ["Team Web has 2 approved urgent requests in 7 days (limit 1)."]
            )


class UrgencyConcurrencyTests(TransactionTestCase):
    def setUp(self) -> None:
        self.boss = create_person(1, "Bob Boss", "boss")
        self.admin = create_person(3, "Ada Admin", "ada")
        self.admin.is_admin = True
        self.admin.save()
        self.author = create_person(2, "Ann Author", "ann")
        self.author.reports_to = self.boss
        self.author.save()
        self.merge_request = create_merge_request(
            self.author, 5, "Card retries", MergeRequestState.IN_REVIEW
        )

    def test_urg_6_two_requests_at_once_leave_exactly_one(self):
        """URG-6: Given an author with no request, when two requests are
        submitted at the same moment, then exactly one is stored and the other
        is refused as already requested."""
        # given
        barrier = threading.Barrier(2)

        def submit(_: int) -> str:
            try:
                barrier.wait()
                urgency.request(self.author, self.merge_request.pk, "Now", "user:ann")
                return "stored"
            except urgency.UrgencyRefused as error:
                return str(error)
            finally:
                connection.close()

        # when
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(submit, range(2)))

        # then
        self.assertEqual(
            results, ["Urgency is already requested for this MR.", "stored"]
        )
        self.assertEqual(UrgencyRequest.objects.count(), 1)
        self.assertEqual(
            AuditEvent.objects.filter(action="urgency.requested").count(), 1
        )

    def test_urg_12_one_decision_wins(self):  # pylint: disable=too-many-locals
        """URG-12: Given a pending request, when two people decide it, or the
        author withdraws it while it is decided, and all wait for the MR lock,
        then exactly one decision applies and the other gets "no longer
        pending"."""
        losers = {
            "approve and withdraw": (
                lambda pk: urgency.approve(self.boss, pk, "", "user:boss"),
                lambda pk: urgency.withdraw(self.author, pk, "user:ann"),
            ),
            "approve and decline": (
                lambda pk: urgency.approve(self.boss, pk, "", "user:boss"),
                lambda pk: urgency.decline(self.admin, pk, "", "user:ada"),
            ),
        }
        for iid, (label, (first, second)) in enumerate(losers.items(), start=10):
            with self.subTest(label):
                # given
                merge_request = create_merge_request(
                    self.author, iid, f"MR {iid}", MergeRequestState.IN_REVIEW
                )
                pending = UrgencyRequest.objects.create(
                    merge_request=merge_request,
                    requester=self.author,
                    approver=self.boss,
                    reason="Now",
                )
                locked = threading.Event()
                release = threading.Event()

                def holder(merge_request=merge_request, locked=locked, release=release):
                    try:
                        with transaction.atomic():
                            MergeRequest.objects.select_for_update().get(
                                pk=merge_request.pk
                            )
                            locked.set()
                            release.wait(10)
                    finally:
                        connection.close()

                def run(command, pk=pending.pk) -> str:
                    try:
                        command(pk)
                        return "done"
                    except urgency.UrgencyRefused as error:
                        return str(error)
                    finally:
                        connection.close()

                # when
                with ThreadPoolExecutor(max_workers=3) as pool:
                    holding = pool.submit(holder)
                    locked.wait(5)
                    results = [pool.submit(run, first), pool.submit(run, second)]
                    try:
                        wait_until_blocked_on_locks(2)
                    finally:
                        release.set()
                    holding.result(timeout=10)
                    outcomes = sorted(result.result(timeout=10) for result in results)

                # then
                self.assertEqual(
                    outcomes, ["This request is no longer pending.", "done"]
                )
                pending.refresh_from_db()
                merge_request.refresh_from_db()
                self.assertEqual(
                    merge_request.urgent_at is not None,
                    pending.status == UrgencyRequestStatus.APPROVED,
                )
                self.assertNotEqual(pending.status, UrgencyRequestStatus.PENDING)
        with self.subTest("a decider who lost is sent back with a message"):
            panel_login(self.client, self.boss.user)

            response = self.client.post(APPROVE_URL.format(pending.pk))

            self.assertRedirects(response, TAB_URL, fetch_redirect_response=False)
            self.assertEqual(
                str(list(get_messages(response.wsgi_request))[-1]),
                "This request is no longer pending.",
            )
