from django.conf import settings
from django.db import models
from django.utils import timezone


def default_working_days() -> list[int]:
    return list(settings.RMO_DEFAULT_WORKING_DAYS)


class Person(models.Model):
    """A developer or admin known to the orchestrator, matched by GitLab user id."""

    gitlab_user_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=128)
    working_days = models.JSONField(default=default_working_days)
    daily_budget_minutes = models.PositiveIntegerField(null=True, blank=True)
    is_admin = models.BooleanField(default=False)


class MergeRequestState(models.TextChoices):  # pylint: disable=too-many-ancestors
    AWAITING_AI_REVIEW = "awaiting_ai_review"
    AWAITING_BOOKING = "awaiting_booking"
    IN_REVIEW = "in_review"
    WAITING_ON_AUTHOR = "waiting_on_author"
    NEEDS_TRIAGE = "needs_triage"


class MergeRequest(models.Model):
    """One GitLab merge request tracked through review."""

    project_id = models.BigIntegerField()
    iid = models.PositiveIntegerField()
    author = models.ForeignKey(Person, on_delete=models.DO_NOTHING, db_constraint=False)
    title = models.CharField(max_length=255)
    state = models.CharField(
        max_length=32,
        choices=MergeRequestState.choices,
        default=MergeRequestState.AWAITING_AI_REVIEW,
    )
    opened_at = models.DateTimeField(default=timezone.now)
    changed_lines = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project_id", "iid"], name="mr_unique_project_iid"
            )
        ]
