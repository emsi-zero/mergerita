"""Forms for the Availability and My reviews pages."""

from django import forms
from django.conf import settings

# Weekday numbers follow `date.weekday()` (Monday is 0); the week starts on Saturday.
WEEKDAY_CHOICES = [
    (5, "Sat"),
    (6, "Sun"),
    (0, "Mon"),
    (1, "Tue"),
    (2, "Wed"),
    (3, "Thu"),
    (4, "Fri"),
]

WEEKDAY_NAMES = dict(WEEKDAY_CHOICES)


class WorkingDaysForm(forms.Form):
    """Working weekdays; at least one (DVP-7)."""

    days = forms.TypedMultipleChoiceField(
        choices=WEEKDAY_CHOICES,
        coerce=int,
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Select at least one working day."},
    )


class BudgetForm(forms.Form):
    """Daily budget from `RMO_DAILY_BUDGET_CHOICES` (DVP-8)."""

    minutes = forms.TypedChoiceField(
        choices=[
            (value, f"{value} min") for value in settings.RMO_DAILY_BUDGET_CHOICES
        ],
        coerce=int,
        widget=forms.RadioSelect,
        error_messages={"required": "Choose a daily budget."},
    )


class LeaveForm(forms.Form):
    """A leave period; the end is not before the start (DVP-9)."""

    start_date = forms.DateField(
        label="From", widget=forms.DateInput(attrs={"type": "date"})
    )
    end_date = forms.DateField(
        label="To", widget=forms.DateInput(attrs={"type": "date"})
    )

    def clean(self) -> dict:
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            raise forms.ValidationError(
                "The end date must not be before the start date."
            )
        return cleaned


class PresenceForm(forms.Form):
    """The optional note of a presence request, limited in length (MRV-7)."""

    note = forms.CharField(
        required=False,
        max_length=settings.RMO_PRESENCE_NOTE_MAX_LENGTH,
    )


class UrgencyRequestForm(forms.Form):
    """The reason of an urgency request, 1 to the maximum length (URG-5)."""

    reason = forms.CharField(max_length=settings.RMO_URGENCY_REASON_MAX_LENGTH)


class UrgencyDecisionForm(forms.Form):
    """The optional note of an urgency decision."""

    note = forms.CharField(required=False)
