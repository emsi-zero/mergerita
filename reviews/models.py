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


class CodeArea(models.Model):
    """A part of the code base with owners and a size limit for one MR."""

    name = models.CharField(max_length=128)
    path_patterns = models.JSONField(default=list)
    size_limit_lines = models.PositiveIntegerField()


class OwnerRole(models.TextChoices):  # pylint: disable=too-many-ancestors
    PRIMARY = "primary"
    BACKUP = "backup"


class Ownership(models.Model):
    """A person who owns a code area."""

    person = models.ForeignKey(Person, on_delete=models.DO_NOTHING, db_constraint=False)
    area = models.ForeignKey(CodeArea, on_delete=models.DO_NOTHING, db_constraint=False)
    role = models.CharField(max_length=16, choices=OwnerRole.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["person", "area"], name="ownership_unique_person_area"
            )
        ]


class AreaReviewState(models.TextChoices):  # pylint: disable=too-many-ancestors
    """Whether an area review has a reviewer booked."""

    UNBOOKED = "unbooked"
    BOOKED = "booked"


class AreaReview(models.Model):
    """One review of an MR that covers one or more code areas."""

    merge_request = models.ForeignKey(
        MergeRequest,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="area_reviews",
    )
    areas = models.ManyToManyField(CodeArea)
    state = models.CharField(
        max_length=16,
        choices=AreaReviewState.choices,
        default=AreaReviewState.UNBOOKED,
    )
    reviewer = models.ForeignKey(
        Person,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="+",
    )
    booked_day = models.DateField(null=True, blank=True)
    slot_minutes = models.PositiveIntegerField(default=0)
    reason = models.TextField(blank=True, default="")
