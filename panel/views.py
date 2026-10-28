from datetime import datetime

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch, Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.generic import DetailView, ListView, RedirectView

from audit.models import AuditEvent
from reviews.models import AreaReview, MergeRequest, MergeRequestState, Person

# Badge label and CSS class per MR state (copied from the mockup).
STATE_BADGES = {
    MergeRequestState.AWAITING_AI_REVIEW: ("AI review", "ai"),
    MergeRequestState.AWAITING_BOOKING: ("Booking", "booking"),
    MergeRequestState.IN_REVIEW: ("In review", "review"),
    MergeRequestState.WAITING_ON_AUTHOR: ("Your move", "author"),
    MergeRequestState.NEEDS_TRIAGE: ("Needs triage", "triage"),
}

STATE_SUMMARIES = {
    MergeRequestState.AWAITING_AI_REVIEW: "Waiting for the AI Reviewer",
    MergeRequestState.AWAITING_BOOKING: "Waiting for reviewers to be booked",
    MergeRequestState.IN_REVIEW: "Reviewers are booked",
    MergeRequestState.WAITING_ON_AUTHOR: "AI Reviewer requested changes",
    MergeRequestState.NEEDS_TRIAGE: "Files outside any code area",
}

# Title and text of the next step for the author, per MR state.
NEXT_STEPS = {
    MergeRequestState.AWAITING_AI_REVIEW: (
        "Waiting for the AI Reviewer",
        "Reviewers are booked as soon as it approves.",
    ),
    MergeRequestState.AWAITING_BOOKING: (
        "Waiting for reviewers to be booked",
        "The AI Reviewer approved. Reviewers are booked from the owners of "
        "each code area.",
    ),
    MergeRequestState.IN_REVIEW: (
        "Nothing to do",
        "Reviewers are booked. You will be notified when they finish "
        "or ask for changes.",
    ),
    MergeRequestState.WAITING_ON_AUTHOR: (
        "Your move: address the AI Reviewer comments",
        "Push a fix to the branch. The MR goes back to the AI Reviewer "
        "automatically; nothing else to click.",
    ),
    MergeRequestState.NEEDS_TRIAGE: (
        "Waiting for the admin",
        "Some changed files are not in any code area, so nobody owns them yet. "
        "The admin was alerted and will add an area or owner.",
    ),
}

ACTION_LABELS = {
    "mr.opened": "Opened",
    "mr.ai_review_verdict": "AI Reviewer gave a verdict",
    "area_review.created": "Area review created",
    "area_review.booked": "Reviewer booked",
    "mr.in_review": "Moved to review",
    "mr.needs_triage": "Needs triage: files outside any code area",
}


class PanelAccessMixin(LoginRequiredMixin):
    """Require a signed-in user linked to a Person (DVP-1, DVP-2).

    Sets `self.person`. Every panel query filters on it (INV-1).
    """

    person: Person

    def dispatch(
        self, request: HttpRequest, *args: object, **kwargs: object
    ) -> HttpResponse:
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        try:
            self.person = request.user.person
        except Person.DoesNotExist:
            return render(request, "panel/not_linked.html", status=403)
        return super().dispatch(request, *args, **kwargs)


class PanelHomeView(PanelAccessMixin, RedirectView):
    """Send the developer to My MRs."""

    pattern_name = "panel:mrs"


class MyMergeRequestsView(
    PanelAccessMixin, ListView
):  # pylint: disable=too-many-ancestors
    """List the signed-in developer's own MRs."""

    template_name = "panel/mrs.html"
    context_object_name = "merge_requests"

    def get_queryset(self) -> QuerySet[MergeRequest]:
        return (
            MergeRequest.objects.filter(author=self.person)
            .prefetch_related("area_reviews")
            .order_by("-opened_at")
        )

    def get_context_data(self, **kwargs: object) -> dict:
        context = super().get_context_data(**kwargs)
        for merge_request in context["merge_requests"]:
            merge_request.badge, merge_request.badge_class = STATE_BADGES[
                merge_request.state
            ]
            merge_request.summary = _summary(merge_request)
        return context


class MergeRequestDetailPanelView(PanelAccessMixin, DetailView):
    """Show one of the developer's own MRs; any other MR is 404."""

    template_name = "panel/mr_detail.html"
    context_object_name = "merge_request"

    def get_queryset(self) -> QuerySet[MergeRequest]:
        return MergeRequest.objects.filter(author=self.person).prefetch_related(
            Prefetch(
                "area_reviews",
                queryset=AreaReview.objects.select_related("reviewer")
                .prefetch_related("areas")
                .order_by("pk"),
            )
        )

    def get_context_data(self, **kwargs: object) -> dict:
        context = super().get_context_data(**kwargs)
        merge_request = context["merge_request"]
        area_reviews = list(merge_request.area_reviews.all())
        merge_request.badge, merge_request.badge_class = STATE_BADGES[
            merge_request.state
        ]
        context["area_reviews"] = area_reviews
        context["next_title"], context["next_text"] = NEXT_STEPS[merge_request.state]
        context["size_warning"] = _size_warning(merge_request, area_reviews)
        context["history"] = _history(merge_request, area_reviews)
        return context


def _summary(merge_request: MergeRequest) -> str:
    """Return the one-line summary of an MR."""
    area_reviews = merge_request.area_reviews.all()
    if not area_reviews:
        return STATE_SUMMARIES[merge_request.state]
    booked = sum(1 for review in area_reviews if review.reviewer_id is not None)
    return f"{booked} of {len(area_reviews)} area reviews booked"


def _size_warning(
    merge_request: MergeRequest, area_reviews: list[AreaReview]
) -> str | None:
    """Name the touched area with the smallest size limit, when the MR exceeds it."""
    areas = [area for review in area_reviews for area in review.areas.all()]
    if not areas:
        return None
    tightest = min(areas, key=lambda area: area.size_limit_lines)
    if merge_request.changed_lines <= tightest.size_limit_lines:
        return None
    return (
        f"Over the {tightest.size_limit_lines}-line limit of the {tightest.name} "
        "area. Smaller MRs get booked sooner. Splitting a large MR may help."
    )


def _history(
    merge_request: MergeRequest, area_reviews: list[AreaReview]
) -> list[tuple[datetime, str]]:
    """Return (time, label) for audit events of the MR and its area reviews."""
    events = AuditEvent.objects.filter(
        Q(
            subject_type=MergeRequest._meta.label,  # pylint: disable=protected-access
            subject_id=str(merge_request.pk),
        )
        | Q(
            subject_type=AreaReview._meta.label,  # pylint: disable=protected-access
            subject_id__in=[str(review.pk) for review in area_reviews],
        )
    ).order_by("created_at", "pk")
    return [
        (event.created_at, ACTION_LABELS.get(event.action, event.action))
        for event in events
    ]
