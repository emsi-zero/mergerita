from django.test import TestCase, override_settings

from integrations.models import InboxEvent

URL = "/webhooks/gitlab/"
PAYLOAD = {"project": {"id": 7}, "object_attributes": {"iid": 3, "action": "open"}}


@override_settings(GITLAB_WEBHOOK_TOKEN="secret")
class GitLabWebhookTest(TestCase):
    def test_wrong_or_missing_token_is_rejected(self) -> None:
        """RMO-1: Given a missing or wrong token, when a webhook arrives,
        then reply 401 and store no event. An unset configured token fails closed."""
        for name, configured, headers in (
            ("missing", "secret", {}),
            ("wrong", "secret", {"HTTP_X_GITLAB_TOKEN": "nope"}),
            ("unset_configured_token", "", {}),
        ):
            with (
                self.subTest(name=name),
                self.settings(GITLAB_WEBHOOK_TOKEN=configured),
            ):
                # given
                headers = {**headers, "HTTP_X_GITLAB_EVENT_UUID": "u-1"}

                # when
                response = self.client.post(
                    URL, PAYLOAD, content_type="application/json", **headers
                )

                # then
                self.assertEqual(response.status_code, 401)
                self.assertEqual(InboxEvent.objects.count(), 0)

    def test_valid_token_stores_one_event(self) -> None:
        """RMO-2: Given a valid token, when a webhook arrives,
        then store one unprocessed event and reply 202."""
        # given
        headers = {
            "HTTP_X_GITLAB_TOKEN": "secret",
            "HTTP_X_GITLAB_EVENT": "Merge Request Hook",
            "HTTP_X_GITLAB_EVENT_UUID": "u-2",
        }

        # when
        response = self.client.post(
            URL, PAYLOAD, content_type="application/json", **headers
        )

        # then
        self.assertEqual(response.status_code, 202)
        event = InboxEvent.objects.get()
        self.assertEqual(event.event_uuid, "u-2")
        self.assertEqual(event.mr_key, "7:3")
        self.assertIsNone(event.processed_at)

    def test_duplicate_uuid_keeps_one_event(self) -> None:
        """RMO-3: Given one UUID delivered twice, when both arrive,
        then keep one event and reply 202 both times."""
        # given
        headers = {
            "HTTP_X_GITLAB_TOKEN": "secret",
            "HTTP_X_GITLAB_EVENT": "Merge Request Hook",
            "HTTP_X_GITLAB_EVENT_UUID": "u-3",
        }

        # when
        first = self.client.post(
            URL, PAYLOAD, content_type="application/json", **headers
        )
        second = self.client.post(
            URL, PAYLOAD, content_type="application/json", **headers
        )

        # then
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(InboxEvent.objects.count(), 1)

    def test_malformed_webhook_gets_400_and_stores_nothing(self) -> None:
        """RMO-20: Given a valid token and a malformed body or too-long header,
        when a webhook arrives, then reply 400 and store no event."""
        mr_headers = {"HTTP_X_GITLAB_EVENT": "Merge Request Hook"}
        note_headers = {"HTTP_X_GITLAB_EVENT": "Note Hook"}
        for name, body, headers in (
            ("not_an_object", "[1, 2]", {}),
            ("missing_project", '{"object_attributes": {"iid": 3}}', mr_headers),
            (
                "missing_iid",
                '{"project": {"id": 7}, "object_attributes": {}}',
                mr_headers,
            ),
            (
                "null_project_id",
                '{"project": {"id": null}, "object_attributes": {"iid": 3}}',
                mr_headers,
            ),
            (
                "note_without_merge_request",
                '{"project": {"id": 7}, "object_attributes": {"noteable_type": "MergeRequest"}}',
                note_headers,
            ),
            (
                "note_without_noteable_type",
                '{"project": {"id": 7}, "merge_request": {"iid": 3}, "object_attributes": {}}',
                note_headers,
            ),
            (
                "note_without_iid",
                '{"project": {"id": 7}, "merge_request": {},'
                ' "object_attributes": {"noteable_type": "MergeRequest"}}',
                note_headers,
            ),
            ("nan_constant", '{"x": NaN}', {}),
            ("uuid_too_long", "{}", {"HTTP_X_GITLAB_EVENT_UUID": "u" * 65}),
            ("event_too_long", "{}", {"HTTP_X_GITLAB_EVENT": "e" * 65}),
        ):
            with self.subTest(name=name):
                # given
                headers = {
                    "HTTP_X_GITLAB_TOKEN": "secret",
                    "HTTP_X_GITLAB_EVENT_UUID": "u-20",
                    **headers,
                }

                # when
                response = self.client.post(
                    URL, body, content_type="application/json", **headers
                )

                # then
                self.assertEqual(response.status_code, 400)
                self.assertEqual(InboxEvent.objects.count(), 0)
