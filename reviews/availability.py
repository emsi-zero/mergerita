"""Availability changes of a person: working days, budget and leave, with audit."""

from datetime import date

from django.db import transaction

from audit.services import record
from reviews.models import Leave, Person


def set_working_days(person: Person, days: list[int], actor: str) -> None:
    """Store the weekdays (0 = Monday) the person works, with one audit event."""
    with transaction.atomic():
        person = Person.objects.select_for_update().get(pk=person.pk)
        before = person.working_days
        person.working_days = days
        person.save(update_fields=["working_days"])
        record(
            actor,
            "availability.working_days_changed",
            person,
            {"working_days": before},
            {"working_days": days},
        )


def set_daily_budget(person: Person, minutes: int, actor: str) -> None:
    """Store the person's daily review budget in minutes, with one audit event."""
    with transaction.atomic():
        person = Person.objects.select_for_update().get(pk=person.pk)
        before = person.daily_budget_minutes
        person.daily_budget_minutes = minutes
        person.save(update_fields=["daily_budget_minutes"])
        record(
            actor,
            "availability.budget_changed",
            person,
            {"daily_budget_minutes": before},
            {"daily_budget_minutes": minutes},
        )


def add_leave(person: Person, start_date: date, end_date: date, actor: str) -> None:
    """Add a leave period for the person, with one audit event."""
    with transaction.atomic():
        Leave.objects.create(person=person, start_date=start_date, end_date=end_date)
        record(
            actor,
            "availability.leave_added",
            person,
            None,
            {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        )


def delete_leave(person: Person, leave_id: int, actor: str) -> None:
    """Delete one leave of the person, with one audit event.

    Raise `Leave.DoesNotExist` when the leave is not the person's (INV-1).
    """
    with transaction.atomic():
        leave = Leave.objects.select_for_update().get(pk=leave_id, person=person)
        leave.delete()
        record(
            actor,
            "availability.leave_deleted",
            person,
            {
                "start_date": leave.start_date.isoformat(),
                "end_date": leave.end_date.isoformat(),
            },
            None,
        )
