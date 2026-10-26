import hmac
import json

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from integrations.inbox import store_event

GITLAB_SOURCE = "gitlab"
MERGE_REQUEST_EVENT = "Merge Request Hook"


@method_decorator(csrf_exempt, name="dispatch")
class GitLabWebhookView(View):
    """Store authenticated GitLab webhooks in the inbox; the worker processes them."""

    def post(self, request: HttpRequest) -> HttpResponse:
        token = request.headers.get("X-Gitlab-Token", "")
        expected = settings.GITLAB_WEBHOOK_TOKEN
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            return HttpResponse(status=401)

        event_uuid = request.headers.get("X-Gitlab-Event-UUID")
        if not event_uuid:
            return JsonResponse({"error": "missing event uuid"}, status=400)
        try:
            payload = json.loads(request.body)
        except ValueError:
            return JsonResponse({"error": "invalid json"}, status=400)

        event_kind = request.headers.get("X-Gitlab-Event", "")
        mr_key = None
        if event_kind == MERGE_REQUEST_EVENT:
            mr_key = f"{payload['project']['id']}:{payload['object_attributes']['iid']}"
        store_event(GITLAB_SOURCE, event_uuid, event_kind, mr_key, payload)
        return HttpResponse(status=202)
