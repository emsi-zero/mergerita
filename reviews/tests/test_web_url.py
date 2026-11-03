from django.test import TestCase

from integrations.models import InboxEvent
from reviews.lifecycle import MERGE_REQUEST_EVENT, RECONCILE_SOURCE, handle_event
from reviews.models import MergeRequest, Person

OLD_URL = "https://gitlab.example.com/old/path/-/merge_requests/3"
NEW_URL = "https://gitlab.example.com/new/path/-/merge_requests/3"


def hook_event(action: str, **attributes: object) -> InboxEvent:
    return InboxEvent(
        source="gitlab",
        event_kind=MERGE_REQUEST_EVENT,
        payload={
            "project": {"id": 7},
            "object_attributes": {
                "iid": 3,
                "action": action,
                "author_id": 11,
                "title": "MR 3",
                **attributes,
            },
        },
    )


class WebUrlTests(TestCase):
    def setUp(self) -> None:
        Person.objects.create(gitlab_user_id=11, name="Author")

    def stored_url(self) -> str | None:
        return MergeRequest.objects.get(project_id=7, iid=3).web_url

    def test_mrv_1_hook_and_reconcile_event_store_and_replace_the_web_url(self):
        """MRV-1: Given a Merge Request Hook or a reconciliation event with the
        MR's web address, when the event is handled, then the address is stored
        and replaces an older one.
        """
        handle_event(hook_event("open", url=OLD_URL))
        self.assertEqual(self.stored_url(), OLD_URL)

        with self.subTest("update hook replaces the address"):
            handle_event(hook_event("update", url=NEW_URL))

            self.assertEqual(self.stored_url(), NEW_URL)

        with self.subTest("reconciliation event replaces the address"):
            event = hook_event("reconcile", web_url=OLD_URL)
            event.source = RECONCILE_SOURCE

            handle_event(event)

            self.assertEqual(self.stored_url(), OLD_URL)

        with self.subTest("stale reconciliation snapshot keeps the newer address"):
            MergeRequest.objects.filter(project_id=7, iid=3).update(
                head_sha="a" * 40, web_url=NEW_URL
            )
            event = hook_event("reconcile", web_url=OLD_URL, sha="b" * 40)
            event.source = RECONCILE_SOURCE

            handle_event(event)

            self.assertEqual(self.stored_url(), NEW_URL)

    def test_mrv_2_address_that_is_not_absolute_http_is_not_stored(self):
        """MRV-2: Given an MR with a stored address, when an event carries an
        address that is not an absolute http or https URL, then the stored address
        stays; given a new MR, then it has no address.
        """
        handle_event(hook_event("open", url=OLD_URL))
        for value in (
            "javascript:alert(1)",
            "/group/project/-/merge_requests/3",
            "//evil.example/x",
            "ftp://host/x",
            "https://",
            "https://example.com/" + "a" * 481,
            "http://[::1",
            ["https://example.com/list"],
        ):
            with self.subTest(value=value):
                handle_event(hook_event("update", url=value))

                self.assertEqual(self.stored_url(), OLD_URL)
        for iid, value in (
            (4, "javascript:alert(1)"),
            (5, "https://example.com/" + "a" * 481),
            (6, "http://[::1"),
        ):
            with self.subTest("open event with a bad address", value=value):
                event = hook_event("open", url=value)
                event.payload["object_attributes"]["iid"] = iid

                handle_event(event)

                created = MergeRequest.objects.get(project_id=7, iid=iid)
                self.assertIsNone(created.web_url)
