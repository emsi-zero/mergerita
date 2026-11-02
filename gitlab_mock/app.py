"""FastAPI app that mimics the GitLab REST v4 routes and webhooks the orchestrator uses.

State is in memory. Routes under /api/v4 follow the GitLab docs, trimmed to the
fields that the orchestrator reads. Routes under /_mock/ control the mock.
"""

import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request, Response

REVIEWER_STATES = (
    "unreviewed",
    "reviewed",
    "requested_changes",
    "approved",
    "unapproved",
)
DEFAULT_TIMEOUT_FAULT_SECONDS = 30.0
# System notes that GitLab writes when a reviewer approves or requests changes.
VERDICT_NOTES = {
    "approved": "approved this merge request",
    "requested_changes": "requested changes",
}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _user_dict(user: dict[str, Any]) -> dict[str, Any]:
    return {"id": user["id"], "username": user["username"], "name": user["name"]}


class MockState:  # pylint: disable=too-many-instance-attributes
    """Users, merge requests, faults and the webhook delivery log."""

    def __init__(
        self, token: str, webhook_url: str | None, webhook_secret: str
    ) -> None:
        self.token = token
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret
        self.webhooks_enabled = webhook_url is not None
        self.lock = threading.RLock()
        self.users: dict[int, dict[str, Any]] = {}
        self.mrs: dict[tuple[int, int], dict[str, Any]] = {}
        self.fault: dict[str, Any] | None = None
        self.deliveries: list[dict[str, Any]] = []
        self._sha_counter = 0

    def user(self, user_id: int) -> dict[str, Any]:
        """Return a known user, or create a placeholder for an unseen id."""
        with self.lock:
            if user_id not in self.users:
                self.users[user_id] = {
                    "id": user_id,
                    "username": f"user{user_id}",
                    "name": f"User {user_id}",
                }
            return self.users[user_id]

    def mr(self, project_id: int, iid: int) -> dict[str, Any]:
        """Return the merge request, or raise a 404."""
        try:
            return self.mrs[(project_id, iid)]
        except KeyError:
            raise HTTPException(404, "404 Not found") from None

    def new_reviewer(self, user_id: int) -> dict[str, Any]:
        """Return a reviewer entry in the unreviewed state."""
        return {"user": self.user(user_id), "state": "unreviewed", "created_at": _now()}

    def new_sha(self) -> str:
        """Return a fresh 40-character commit sha."""
        with self.lock:
            self._sha_counter += 1
            return f"{self._sha_counter:040x}"

    def add_note(
        self, mr: dict[str, Any], user_id: int, body: str, system: bool = False
    ) -> None:
        """Record a note by a user on the MR. A system note is how GitLab logs a review."""
        with self.lock:
            mr["notes"].append(
                {
                    "id": sum(len(m["notes"]) for m in self.mrs.values()) + 1,
                    "body": body,
                    "author": _user_dict(self.user(user_id)),
                    "created_at": _now(),
                    "system": system,
                }
            )

    def touch(self, mr: dict[str, Any]) -> None:
        """Set updated_at to now."""
        mr["updated_at"] = _now()

    def mr_json(self, mr: dict[str, Any]) -> dict[str, Any]:
        """Return the MR as GitLab's merge request JSON."""
        return {
            "id": mr["id"],
            "iid": mr["iid"],
            "project_id": mr["project_id"],
            "title": mr["title"],
            "state": mr["state"],
            "author": _user_dict(mr["author"]),
            "reviewers": [_user_dict(r["user"]) for r in mr["reviewers"]],
            "created_at": mr["created_at"],
            "updated_at": mr["updated_at"],
            "sha": mr["sha"],
            "labels": [],
        }

    def reviewers_json(self, mr: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the MR reviewers as GitLab's reviewers JSON."""
        return [
            {
                "user": _user_dict(r["user"]),
                "state": r["state"],
                "created_at": r["created_at"],
            }
            for r in mr["reviewers"]
        ]

    def dump(self) -> dict[str, Any]:
        """Return the whole mock state for the state endpoint."""
        with self.lock:
            return {
                "users": list(self.users.values()),
                "merge_requests": [
                    {
                        **self.mr_json(mr),
                        "reviewer_states": self.reviewers_json(mr),
                        "diffs": mr["diffs"],
                    }
                    for mr in self.mrs.values()
                ],
                "webhooks_enabled": self.webhooks_enabled,
                "fault": self.fault,
                "deliveries": self.deliveries,
            }

    def set_fault(self, body: dict[str, Any]) -> None:
        """Fail the next body["count"] API calls; a count of 0 clears the fault."""
        with self.lock:
            if body.get("count", 0) <= 0:
                self.fault = None
                return
            self.fault = {
                "remaining": body["count"],
                "status": body.get("status", 500),
                "timeout": bool(body.get("timeout", False)),
                "timeout_seconds": body.get(
                    "timeout_seconds", DEFAULT_TIMEOUT_FAULT_SECONDS
                ),
            }

    def take_fault(self) -> dict[str, Any] | None:
        """Consume one call of the active fault, if any."""
        with self.lock:
            if self.fault is None:
                return None
            fault = dict(self.fault)
            remaining = fault["remaining"] - 1
            self.fault = {**fault, "remaining": remaining} if remaining > 0 else None
            return fault

    def mr_hook(
        self, mr: dict[str, Any], action: str, actor: dict[str, Any], **extra: Any
    ) -> dict[str, Any]:
        """Return a Merge Request Hook payload."""
        attributes = {
            "id": mr["id"],
            "iid": mr["iid"],
            "action": action,
            "author_id": mr["author"]["id"],
            "title": mr["title"],
            "state": mr["state"],
            "created_at": mr["created_at"],
            "updated_at": mr["updated_at"],
            **extra,
        }
        return {
            "object_kind": "merge_request",
            "event_type": "merge_request",
            "user": _user_dict(actor),
            "project": {"id": mr["project_id"]},
            "object_attributes": attributes,
            "reviewers": [_user_dict(r["user"]) for r in mr["reviewers"]],
            "labels": [],
        }

    def note_hook(
        self, mr: dict[str, Any], actor: dict[str, Any], note: str
    ) -> dict[str, Any]:
        """Return a Note Hook payload."""
        return {
            "object_kind": "note",
            "event_type": "note",
            "user": _user_dict(actor),
            "project": {"id": mr["project_id"]},
            "object_attributes": {
                "noteable_type": "MergeRequest",
                "note": note,
                "updated_at": mr["updated_at"],
            },
            "merge_request": {"iid": mr["iid"], "id": mr["id"], "title": mr["title"]},
        }

    def send_webhook(self, event: str, payload: dict[str, Any]) -> None:
        """POST a webhook when enabled. A delivery failure is logged, not raised."""
        if not self.webhooks_enabled or not self.webhook_url:
            return
        headers = {
            "X-Gitlab-Token": self.webhook_secret,
            "X-Gitlab-Event": event,
            "X-Gitlab-Event-UUID": str(uuid.uuid4()),
        }
        record: dict[str, Any] = {
            "event": event,
            "uuid": headers["X-Gitlab-Event-UUID"],
        }
        try:
            response = httpx.post(
                self.webhook_url, json=payload, headers=headers, timeout=10
            )
            record["status"] = response.status_code
        except httpx.HTTPError as error:
            record["error"] = type(error).__name__
        with self.lock:
            self.deliveries.append(record)


def _api_router(state: MockState) -> APIRouter:
    """Routes under /api/v4, guarded by the token and the fault injector."""

    def api_guard(request: Request) -> None:
        if request.headers.get("PRIVATE-TOKEN") != state.token:
            raise HTTPException(401, "401 Unauthorized")
        fault = state.take_fault()
        if fault is None:
            return
        if fault["timeout"]:
            time.sleep(fault["timeout_seconds"])
            raise HTTPException(504, "mock timeout")
        raise HTTPException(fault["status"], "mock fault")

    router = APIRouter(prefix="/api/v4", dependencies=[Depends(api_guard)])

    @router.get("/projects/{project_id}/merge_requests/{iid}")
    def get_mr(project_id: int, iid: int) -> dict[str, Any]:
        return state.mr_json(state.mr(project_id, iid))

    @router.get("/projects/{project_id}/merge_requests/{iid}/diffs")
    def get_diffs(
        project_id: int, iid: int, request: Request, response: Response
    ) -> list[dict[str, Any]]:
        return _paged(state.mr(project_id, iid)["diffs"], request, response)

    @router.get("/projects/{project_id}/merge_requests/{iid}/notes")
    def get_notes(
        project_id: int, iid: int, request: Request, response: Response
    ) -> list[dict[str, Any]]:
        with state.lock:
            notes = list(state.mr(project_id, iid)["notes"])
        newest_first = request.query_params.get("sort", "desc") == "desc"
        notes.sort(key=lambda n: (n["created_at"], n["id"]), reverse=newest_first)
        return _paged(notes, request, response)

    @router.get(
        "/projects/{project_id}/merge_requests/{iid}/reviewers",
    )
    def get_reviewers(project_id: int, iid: int) -> list[dict[str, Any]]:
        return state.reviewers_json(state.mr(project_id, iid))

    @router.put("/projects/{project_id}/merge_requests/{iid}")
    def update_mr(
        project_id: int, iid: int, body: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        mr = state.mr(project_id, iid)
        with state.lock:
            if "reviewer_ids" in body:
                old = {r["user"]["id"]: r for r in mr["reviewers"]}
                mr["reviewers"] = [
                    old.get(user_id) or state.new_reviewer(user_id)
                    for user_id in body["reviewer_ids"]
                ]
            state.touch(mr)
            return state.mr_json(mr)

    @router.get("/projects/{project_id}/merge_requests")
    def list_mrs(
        project_id: int, request: Request, response: Response
    ) -> list[dict[str, Any]]:
        query = request.query_params
        mr_state = query.get("state", "all")
        updated_after = query.get("updated_after")
        order_by = query.get("order_by", "created_at")
        sort = query.get("sort", "desc")
        with state.lock:
            mrs = [m for m in state.mrs.values() if m["project_id"] == project_id]
            if mr_state != "all":
                mrs = [m for m in mrs if m["state"] == mr_state]
            if updated_after:
                threshold = _parse_time(updated_after)
                mrs = [m for m in mrs if _parse_time(m["updated_at"]) >= threshold]
            mrs.sort(key=lambda m: m[order_by], reverse=sort == "desc")
            return _paged([state.mr_json(m) for m in mrs], request, response)

    return router


def _control_router(state: MockState) -> APIRouter:
    """Routes under /_mock/ that drive the mock."""
    router = APIRouter(prefix="/_mock")

    @router.post("/mrs")
    def control_open(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        project_id = body["project_id"]
        with state.lock:
            iid = 1 + max((k[1] for k in state.mrs if k[0] == project_id), default=0)
            author = state.user(body["author_id"])
            if "username" in body:
                author["username"] = body["username"]
                author["name"] = body.get("name", body["username"])
            now = _now()
            mr = {
                "id": len(state.mrs) + 1,
                "iid": iid,
                "project_id": project_id,
                "title": body.get("title", f"MR {iid}"),
                "state": "opened",
                "author": author,
                "reviewers": [
                    state.new_reviewer(user_id)
                    for user_id in body.get("reviewer_ids", [])
                ],
                "diffs": body.get("diffs", []),
                "notes": [],
                "sha": state.new_sha(),
                "created_at": now,
                "updated_at": now,
            }
            state.mrs[(project_id, iid)] = mr
            payload = state.mr_hook(mr, "open", author)
            result = state.mr_json(mr)
        state.send_webhook("Merge Request Hook", payload)
        return result

    @router.post("/mrs/{project_id}/{iid}/reviewers/{user_id}/state")
    def control_review(
        project_id: int, iid: int, user_id: int, body: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        new_state = body["state"]
        if new_state not in REVIEWER_STATES:
            raise HTTPException(422, f"state must be one of {REVIEWER_STATES}")
        mr = state.mr(project_id, iid)
        with state.lock:
            entry = next(
                (r for r in mr["reviewers"] if r["user"]["id"] == user_id), None
            )
            if entry is None:
                raise HTTPException(404, "user is not a reviewer")
            state.add_note(
                mr,
                user_id,
                VERDICT_NOTES.get(new_state, new_state),
                system=new_state in VERDICT_NOTES,
            )
            entry["state"] = new_state
            state.touch(mr)
            actor = entry["user"]
            if new_state == "approved":
                event, payload = "Merge Request Hook", state.mr_hook(
                    mr, "approval", actor
                )
            else:
                event, payload = "Note Hook", state.note_hook(mr, actor, new_state)
            result = state.reviewers_json(mr)
        state.send_webhook(event, payload)
        return {"reviewers": result}

    @router.post("/mrs/{project_id}/{iid}/reviewers")
    def control_assign(
        project_id: int, iid: int, body: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        mr = state.mr(project_id, iid)
        with state.lock:
            previous = [_user_dict(r["user"]) for r in mr["reviewers"]]
            if body["user_id"] not in [u["id"] for u in previous]:
                mr["reviewers"].append(state.new_reviewer(body["user_id"]))
            state.touch(mr)
            payload = state.mr_hook(mr, "update", mr["author"])
            payload["changes"] = {
                "reviewers": {"previous": previous, "current": payload["reviewers"]}
            }
            result = state.reviewers_json(mr)
        state.send_webhook("Merge Request Hook", payload)
        return {"reviewers": result}

    return router


def _activity_router(state: MockState) -> APIRouter:
    """Routes under /_mock/ for pushes and notes."""
    router = APIRouter(prefix="/_mock")

    @router.post("/mrs/{project_id}/{iid}/push")
    def control_push(
        project_id: int, iid: int, body: dict[str, Any] = Body(default={})
    ) -> dict[str, Any]:
        mr = state.mr(project_id, iid)
        with state.lock:
            oldrev = mr["sha"]
            mr["sha"] = state.new_sha()
            if "diffs" in body:
                mr["diffs"] = body["diffs"]
            state.touch(mr)
            payload = state.mr_hook(mr, "update", mr["author"], oldrev=oldrev)
            result = state.mr_json(mr)
        state.send_webhook("Merge Request Hook", payload)
        return result

    @router.post("/mrs/{project_id}/{iid}/notes")
    def control_note(
        project_id: int, iid: int, body: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        mr = state.mr(project_id, iid)
        with state.lock:
            actor = state.user(body["user_id"])
            state.add_note(mr, body["user_id"], body["note"])
            state.touch(mr)
            payload = state.note_hook(mr, actor, body["note"])
        state.send_webhook("Note Hook", payload)
        return {"ok": True}

    return router


def _admin_router(state: MockState) -> APIRouter:
    """Routes under /_mock/ for webhook switch, fault injection and state dump."""
    router = APIRouter(prefix="/_mock")

    @router.post("/webhooks")
    def control_webhooks(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        state.webhooks_enabled = bool(body["enabled"])
        return {"webhooks_enabled": state.webhooks_enabled}

    @router.post("/fault")
    def control_fault(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        state.set_fault(body)
        return {"fault": state.fault}

    @router.get("/state")
    def control_state() -> dict[str, Any]:
        return state.dump()

    return router


def _paged(items: list, request: Request, response: Response) -> list:
    page = max(int(request.query_params.get("page", 1)), 1)
    per_page = min(max(int(request.query_params.get("per_page", 20)), 1), 100)
    start = (page - 1) * per_page
    has_next = start + per_page < len(items)
    response.headers["X-Page"] = str(page)
    response.headers["X-Per-Page"] = str(per_page)
    response.headers["X-Next-Page"] = str(page + 1) if has_next else ""
    response.headers["X-Total"] = str(len(items))
    response.headers["X-Total-Pages"] = str(-(-len(items) // per_page))
    return items[start : start + per_page]


def create_app(
    token: str, webhook_url: str | None = None, webhook_secret: str = ""
) -> FastAPI:
    """Build a mock GitLab app with empty state."""
    state = MockState(token, webhook_url, webhook_secret)
    app = FastAPI(title="gitlab_mock")
    app.state.mock = state
    app.include_router(_api_router(state))
    app.include_router(_control_router(state))
    app.include_router(_activity_router(state))
    app.include_router(_admin_router(state))
    return app
