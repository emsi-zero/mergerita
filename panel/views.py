from collections.abc import Callable
from datetime import date, datetime, timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F, OuterRef, Prefetch, Q, QuerySet, Subquery
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.generic import DetailView, ListView, RedirectView, TemplateView, View

from audit.models import AuditEvent
from panel.forms import (
    WEEKDAY_NAMES,
    BudgetForm,
    LeaveForm,
    PresenceForm,
    WorkingDaysForm,
)
from reviews import availability, outcomes, presence
from reviews.booking import person_budget
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CapacityDay,
    Leave,
    MergeAssignment,
    MergeRequest,
    MergeRequestState,
    Person,
    PresenceRequest,
    PresenceRequestState,
    ReviewSession,
    ReviewSessionOutcome,
    default_working_days,
)

# Badge label and CSS class per MR state (copied from the mockup).
STATE_BADGES = {
    MergeRequestState.AWAITING_AI_REVIEW: ("AI review", "ai"),
    MergeRequestState.AWAITING_BOOKING: ("Booking", "booking"),
    MergeRequestState.IN_REVIEW: ("In review", "review"),
    MergeRequestState.WAITING_ON_AUTHOR: ("Your move", "author"),
    MergeRequestState.NEEDS_TRIAGE: ("Needs triage", "triage"),
    MergeRequestState.APPROVED: ("Approved", "review"),
    MergeRequestState.MERGED: ("Merged", "review"),
}

STATE_SUMMARIES = {
    MergeRequestState.AWAITING_AI_REVIEW: "Waiting for the AI Reviewer",
    MergeRequestState.AWAITING_BOOKING: "Waiting for reviewers to be booked",
    MergeRequestState.IN_REVIEW: "Reviewers are booked",
    MergeRequestState.WAITING_ON_AUTHOR: "AI Reviewer requested changes",
    MergeRequestState.NEEDS_TRIAGE: "Files outside any code area",
    MergeRequestState.APPROVED: "All areas approved",
    MergeRequestState.MERGED: "Merged",
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
    MergeRequestState.APPROVED: (
        "Nothing to do",
        "All areas approved. Waiting for merge routing.",
    ),
    MergeRequestState.MERGED: ("Nothing to do", "The MR is merged."),
}

ACTION_LABELS = {
    "mr.opened": "Opened",
    "mr.ai_review_verdict": "AI Reviewer gave a verdict",
    "area_review.created": "Area review created",
    "area_review.booked": "Reviewer booked",
    "mr.in_review": "Moved to review",
    "mr.needs_triage": "Needs triage: files outside any code area",
    "area_review.approved": "Area approved",
    "area_review.approval_reset": "Area approval lost",
    "mr.changes_requested": "Reviewer requested changes",
    "mr.approved": "Approved",
    "mr.merged": "Merged",
    "merge.assigned": "Merge owner assigned",
    "merge.cancelled": "Merge assignment cancelled",
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
            ),
            Prefetch(
                "area_reviews__presence_requests",
                queryset=_active_presence().select_related("requested_by"),
                to_attr="active_presence",
            ),
        )

    def get_context_data(self, **kwargs: object) -> dict:
        context = super().get_context_data(**kwargs)
        merge_request = context["merge_request"]
        area_reviews = list(merge_request.area_reviews.all())
        merge_request.badge, merge_request.badge_class = STATE_BADGES[
            merge_request.state
        ]
        context["area_reviews"] = area_reviews
        today = timezone.localdate()
        context["presence_requests"] = [
            (review, ask)
            for review in area_reviews
            if review.booked_day and review.booked_day >= today
            for ask in review.active_presence
        ]
        context["next_title"], context["next_text"] = NEXT_STEPS[merge_request.state]
        context["size_warning"] = _size_warning(merge_request, area_reviews)
        context["history"] = _history(merge_request, area_reviews)
        return context


class MyReviewsView(PanelAccessMixin, TemplateView):
    """Show the area reviews booked to the developer, grouped by day (DVP-6)."""

    template_name = "panel/reviews.html"

    def get_context_data(self, **kwargs: object) -> dict:
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        last_day = today + timedelta(days=settings.RMO_BOOKING_HORIZON_DAYS - 1)
        latest = ReviewSession.objects.filter(area_review=OuterRef("pk")).order_by(
            "-pk"
        )
        reviews = (
            AreaReview.objects.filter(
                reviewer=self.person,
                state=AreaReviewState.BOOKED,
            )
            .select_related("merge_request__author")
            .prefetch_related(
                "areas",
                Prefetch(
                    "presence_requests",
                    queryset=_active_presence(),
                    to_attr="active_presence",
                ),
            )
            .annotate(
                session_id=Subquery(latest.values("pk")[:1]),
                session_outcome=Subquery(latest.values("outcome")[:1]),
                session_continuation=Subquery(latest.values("is_continuation")[:1]),
            )
            .filter(
                Q(booked_day__range=(today, last_day))
                | Q(
                    booked_day__lt=today,
                    session_outcome=ReviewSessionOutcome.PENDING,
                )
            )
            .order_by(
                "booked_day",
                F("session_continuation").desc(nulls_last=True),
                "merge_request__opened_at",
                "pk",
            )
        )
        reviews_by_day: dict[date, list[AreaReview]] = {}
        overdue: list[AreaReview] = []
        for review in reviews:
            review.outcome_label = (
                ReviewSessionOutcome(review.session_outcome).label
                if review.session_outcome
                else ""
            )
            if review.booked_day < today:
                overdue.append(review)
            else:
                reviews_by_day.setdefault(review.booked_day, []).append(review)
        capacity = {
            row.day: row
            for row in CapacityDay.objects.filter(
                person=self.person, day__in=reviews_by_day
            )
        }
        context["today"] = today
        context["overdue"] = overdue
        context["horizon_days"] = settings.RMO_BOOKING_HORIZON_DAYS
        context["note_max_length"] = settings.RMO_PRESENCE_NOTE_MAX_LENGTH
        context["days"] = [
            self._day(day, day_reviews, capacity.get(day))
            for day, day_reviews in reviews_by_day.items()
        ]
        return context

    def _day(
        self, day: date, reviews: list[AreaReview], row: CapacityDay | None
    ) -> dict:
        """Return the booked minutes and budget of one day with its reviews."""
        booked = row.booked_minutes if row else 0
        budget = person_budget(self.person)
        return {
            "day": day,
            "reviews": reviews,
            "booked": booked,
            "budget": budget,
            "over": booked > budget,
        }


class MyMergesView(PanelAccessMixin, TemplateView):
    """List the open merge assignments of a merge owner, in window order (MRG-4)."""

    template_name = "panel/merges.html"

    def get(
        self, request: HttpRequest, *args: object, **kwargs: object
    ) -> HttpResponse:
        if not self.person.is_merge_owner:
            raise Http404
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs: object) -> dict:
        context = super().get_context_data(**kwargs)
        assignments = list(
            MergeAssignment.objects.filter(
                merge_owner=self.person,
                merged_at__isnull=True,
                cancelled_at__isnull=True,
            )
            .select_related("merge_request__author")
            .prefetch_related(
                Prefetch(
                    "merge_request__area_reviews",
                    queryset=AreaReview.objects.select_related(
                        "reviewer"
                    ).prefetch_related("areas"),
                )
            )
            .order_by("window", "pk")
        )
        for assignment in assignments:
            area_reviews = assignment.merge_request.area_reviews.all()
            assignment.areas = sorted(
                {area.name for review in area_reviews for area in review.areas.all()}
            )
            assignment.approvers = sorted(
                {review.reviewer.name for review in area_reviews if review.reviewer}
            )
        context["assignments"] = assignments
        return context


class RequestPresenceView(MyReviewsView):  # pylint: disable=too-many-ancestors
    """Ask the author to join one of the developer's booked reviews (MRV-4)."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        form = PresenceForm(request.POST)
        if not form.is_valid():
            context = self.get_context_data(presence_form=form, presence_review_id=pk)
            return self.render_to_response(context, status=400)
        try:
            requested = presence.request_presence(
                self.person,
                pk,
                form.cleaned_data["note"],
                f"user:{request.user.username}",
            )
        except AreaReview.DoesNotExist as error:
            raise Http404 from error
        if requested:
            messages.success(request, "The author was asked to join.")
        return redirect("panel:reviews")


class CancelPresenceView(PanelAccessMixin, View):
    """Withdraw the request for the author on one of the developer's reviews (MRV-6)."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        try:
            withdrawn = presence.withdraw_presence(
                self.person, pk, f"user:{request.user.username}"
            )
        except AreaReview.DoesNotExist as error:
            raise Http404 from error
        if withdrawn:
            messages.success(request, "The request was withdrawn.")
        return redirect("panel:reviews")


class SessionOutcomeView(PanelAccessMixin, View):
    """Report an outcome on the developer's pending session (RVO-2..5)."""

    http_method_names = ["post"]
    outcome: Callable[[Person, int, str], object]
    message: str

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        try:
            self.outcome(self.person, pk, f"user:{request.user.username}")
        except ReviewSession.DoesNotExist as error:
            raise Http404 from error
        messages.success(request, self.message)
        return redirect("panel:reviews")


class SessionDoneView(SessionOutcomeView):
    """Mark the pending session done (RVO-2)."""

    outcome = staticmethod(outcomes.mark_done)
    message = "Marked as done."


class SessionMoreTimeView(SessionOutcomeView):
    """Close the pending session and book the next one with the same reviewer (RVO-3)."""

    outcome = staticmethod(outcomes.need_more_time)
    message = "More time requested."


class SessionHandBackView(SessionOutcomeView):
    """Close the pending session and book another owner (RVO-4)."""

    outcome = staticmethod(outcomes.hand_back)
    message = "Review handed back."


class AvailabilityView(PanelAccessMixin, TemplateView):
    """Show the developer's working days, budget and leave."""

    template_name = "panel/availability.html"

    def get_context_data(self, **kwargs: object) -> dict:
        context = super().get_context_data(**kwargs)
        context.setdefault(
            "days_form", WorkingDaysForm(initial={"days": self.person.working_days})
        )
        context.setdefault(
            "budget_form",
            BudgetForm(initial={"minutes": person_budget(self.person)}),
        )
        context.setdefault("leave_form", LeaveForm())
        context["leaves"] = Leave.objects.filter(person=self.person).order_by(
            "start_date", "pk"
        )
        context["default_budget"] = settings.RMO_DEFAULT_DAILY_BUDGET_MINUTES
        context["default_days"] = ", ".join(
            WEEKDAY_NAMES[day] for day in default_working_days()
        )
        return context


class AvailabilityFormView(AvailabilityView):  # pylint: disable=too-many-ancestors
    """Base of the availability posts: validate, save, redirect (DVP-7 to DVP-9)."""

    http_method_names = ["post"]
    form_class: type[forms.Form]
    form_context_name: str
    success_message: str

    def save(self, cleaned_data: dict, actor: str) -> None:
        """Store the validated data through `reviews.availability`."""
        raise NotImplementedError

    def post(self, request: HttpRequest) -> HttpResponse:
        form = self.form_class(request.POST)
        if not form.is_valid():
            context = self.get_context_data(**{self.form_context_name: form})
            return self.render_to_response(context, status=400)
        self.save(form.cleaned_data, f"user:{request.user.username}")
        messages.success(request, self.success_message)
        return redirect("panel:availability")


class WorkingDaysView(AvailabilityFormView):
    """Save the developer's working days."""

    # pylint: disable=too-many-ancestors
    form_class = WorkingDaysForm
    form_context_name = "days_form"
    success_message = "Working days saved."

    def save(self, cleaned_data: dict, actor: str) -> None:
        availability.set_working_days(self.person, sorted(cleaned_data["days"]), actor)


class BudgetView(AvailabilityFormView):
    """Save the developer's daily review budget."""

    # pylint: disable=too-many-ancestors
    form_class = BudgetForm
    form_context_name = "budget_form"
    success_message = "Daily budget saved."

    def save(self, cleaned_data: dict, actor: str) -> None:
        availability.set_daily_budget(self.person, cleaned_data["minutes"], actor)


class AddLeaveView(AvailabilityFormView):
    """Add a leave period for the developer."""

    # pylint: disable=too-many-ancestors
    form_class = LeaveForm
    form_context_name = "leave_form"
    success_message = "Leave added."

    def save(self, cleaned_data: dict, actor: str) -> None:
        availability.add_leave(
            self.person, cleaned_data["start_date"], cleaned_data["end_date"], actor
        )


class DeleteLeaveView(PanelAccessMixin, View):
    """Delete one of the developer's own leaves; any other leave is 404."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        try:
            availability.delete_leave(self.person, pk, f"user:{request.user.username}")
        except Leave.DoesNotExist as error:
            raise Http404 from error
        messages.success(request, "Leave removed.")
        return redirect("panel:availability")


def _active_presence() -> QuerySet[PresenceRequest]:
    """Return the presence requests that are not withdrawn."""
    return PresenceRequest.objects.filter(state=PresenceRequestState.ACTIVE)


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
    return [(event.created_at, _history_label(event)) for event in events]


def _history_label(event: AuditEvent) -> str:
    """Return the label of an audit event.

    A merge names what is odd about it (MRG-8): outside its window, by another
    person than the merge owner, or with no merge owner assigned.
    """
    label = ACTION_LABELS.get(event.action, event.action)
    if event.action != "mr.merged":
        return label
    after = event.after
    notes = [
        text
        for flag, text in (
            (after["outside_window"], "outside its window"),
            (after["by_other_person"], "not by the assigned merge owner"),
            (after["merge_owner"] is None, "no merge owner assigned"),
        )
        if flag
    ]
    return f"{label} ({', '.join(notes)})" if notes else label
