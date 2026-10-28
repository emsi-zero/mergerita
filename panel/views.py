from datetime import date, datetime, timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Prefetch, Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.generic import DetailView, ListView, RedirectView, TemplateView, View

from audit.models import AuditEvent
from panel.forms import WEEKDAY_NAMES, BudgetForm, LeaveForm, WorkingDaysForm
from reviews import availability
from reviews.booking import person_budget
from reviews.models import (
    AreaReview,
    AreaReviewState,
    CapacityDay,
    Leave,
    MergeRequest,
    MergeRequestState,
    Person,
    default_working_days,
)

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


class MyReviewsView(PanelAccessMixin, TemplateView):
    """Show the area reviews booked to the developer, grouped by day (DVP-6)."""

    template_name = "panel/reviews.html"

    def get_context_data(self, **kwargs: object) -> dict:
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        last_day = today + timedelta(days=settings.RMO_BOOKING_HORIZON_DAYS - 1)
        reviews = (
            AreaReview.objects.filter(
                reviewer=self.person,
                state=AreaReviewState.BOOKED,
                booked_day__range=(today, last_day),
            )
            .select_related("merge_request__author")
            .prefetch_related("areas")
            .order_by("booked_day", "pk")
        )
        reviews_by_day: dict[date, list[AreaReview]] = {}
        for review in reviews:
            reviews_by_day.setdefault(review.booked_day, []).append(review)
        capacity = {
            row.day: row
            for row in CapacityDay.objects.filter(
                person=self.person, day__in=reviews_by_day
            )
        }
        context["today"] = today
        context["horizon_days"] = settings.RMO_BOOKING_HORIZON_DAYS
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
