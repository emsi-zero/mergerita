from django.conf import settings
from django.db import models
from django.db.models.functions import Now
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
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="person",
    )


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
    # When the MR last entered the AI gate: opened, or new commits pushed.
    ai_gate_entered_at = models.DateTimeField(default=timezone.now, db_default=Now())
    changed_lines = models.PositiveIntegerField(default=0)
    # Head commit sha last seen from GitLab. Null for MRs stored before it was recorded.
    head_sha = models.CharField(max_length=64, null=True, blank=True)
    # When that sha was pushed (GitLab's clock). An older reconcile snapshot is ignored.
    head_sha_at = models.DateTimeField(null=True, blank=True)
    # GitLab's address of the MR. Null until a hook or reconciliation carries it.
    web_url = models.URLField(max_length=500, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project_id", "iid"], name="mr_unique_project_iid"
            )
        ]


class ReconcileCursor(models.Model):
    """How far reconciliation has read one project. Null means "read all open MRs"."""

    project_id = models.BigIntegerField(unique=True)
    updated_after = models.DateTimeField(null=True, blank=True)


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


class PresenceRequestState(models.TextChoices):  # pylint: disable=too-many-ancestors
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class PresenceRequest(models.Model):
    """A reviewer's request that the MR author joins the review session."""

    area_review = models.ForeignKey(
        AreaReview,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="presence_requests",
    )
    requested_by = models.ForeignKey(
        Person, on_delete=models.DO_NOTHING, db_constraint=False, related_name="+"
    )
    note = models.TextField(blank=True, default="", db_default="")
    state = models.CharField(
        max_length=16,
        choices=PresenceRequestState.choices,
        default=PresenceRequestState.ACTIVE,
        db_default=PresenceRequestState.ACTIVE,
    )
    created_at = models.DateTimeField(default=timezone.now, db_default=Now())
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["area_review"],
                condition=models.Q(state=PresenceRequestState.ACTIVE),
                name="presence_one_active",
            )
        ]


class Leave(models.Model):
    """A period when a person cannot review. Both dates are included."""

    person = models.ForeignKey(Person, on_delete=models.DO_NOTHING, db_constraint=False)
    start_date = models.DateField()
    end_date = models.DateField()


class CapacityDay(models.Model):
    """Review minutes booked for one person on one day (INV-3)."""

    person = models.ForeignKey(Person, on_delete=models.DO_NOTHING, db_constraint=False)
    day = models.DateField()
    budget_minutes = models.PositiveIntegerField()
    booked_minutes = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["person", "day"], name="capacity_unique_person_day"
            )
        ]
