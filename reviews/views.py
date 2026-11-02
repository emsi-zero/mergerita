import hmac
import json

from django.conf import settings
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response

from integrations.inbox import store_event
from reviews.lifecycle import MERGE_REQUEST_EVENT, NOTE_EVENT
from reviews.models import AreaReview, MergeRequest
from reviews.serializers import serialize_merge_request

GITLAB_SOURCE = "gitlab"
MAX_FIELD_LENGTH = 64  # InboxEvent.event_uuid, event_kind and mr_key


def _derive_mr_key(event_kind: str, payload: object) -> str | None:
    """Return "<project_id>:<iid>" for a merge-request or MR note event, else None.

    Raise ValueError, KeyError or TypeError when the payload is malformed.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload is not a JSON object")
    if event_kind == MERGE_REQUEST_EVENT:
        iid = payload["object_attributes"]["iid"]
    elif (
        event_kind == NOTE_EVENT
        and payload["object_attributes"]["noteable_type"] == "MergeRequest"
    ):
        iid = payload["merge_request"]["iid"]
    else:
        return None
    project_id = payload["project"]["id"]
    for value in (project_id, iid):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("project.id and iid must be positive integers")
    return f"{project_id}:{iid}"


def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not valid JSON")


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
        event_kind = request.headers.get("X-Gitlab-Event", "")
        try:
            payload = json.loads(request.body, parse_constant=_reject_constant)
            mr_key = _derive_mr_key(event_kind, payload)
        except (ValueError, KeyError, TypeError, RecursionError):
            return JsonResponse({"error": "malformed webhook"}, status=400)
        if (
            len(event_uuid) > MAX_FIELD_LENGTH
            or len(event_kind) > MAX_FIELD_LENGTH
            or (mr_key is not None and len(mr_key) > MAX_FIELD_LENGTH)
        ):
            return JsonResponse({"error": "header too long"}, status=400)
        store_event(GITLAB_SOURCE, event_uuid, event_kind, mr_key, payload)
        return HttpResponse(status=202)


class MergeRequestDetailView(RetrieveAPIView):
    """Read-only MR detail for staff: state and booking explanations."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser]
    queryset = MergeRequest.objects.prefetch_related(
        Prefetch(
            "area_reviews",
            queryset=AreaReview.objects.select_related("reviewer")
            .prefetch_related("areas")
            .order_by("id"),
        )
    )

    def retrieve(self, request: Request, *args: object, **kwargs: object) -> Response:
        return Response(serialize_merge_request(self.get_object()))
