import re
from datetime import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Now
from django.utils import timezone

MERGE_TIME_PATTERN = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def validate_merge_times(value: list) -> None:
    """Reject an entry that is not a string `HH:MM` from 00:00 to 23:59 (MRG-10)."""
    bad = [
        entry
        for entry in value
        if not isinstance(entry, str) or not MERGE_TIME_PATTERN.fullmatch(entry)
    ]
    if bad:
        raise ValidationError(f"Not a valid HH:MM time (00:00-23:59): {bad}")


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
    is_merge_owner = models.BooleanField(default=False, db_default=False)
    # Merge window start times as "HH:MM", in the app's time zone.
    merge_times = models.JSONField(
        default=list, db_default=[], blank=True, validators=[validate_merge_times]
    )
    # None means the default limit of MergeRoutingSettings.
    daily_merge_limit = models.PositiveIntegerField(null=True, blank=True)


class MergeRequestState(models.TextChoices):  # pylint: disable=too-many-ancestors
    AWAITING_AI_REVIEW = "awaiting_ai_review"
    AWAITING_BOOKING = "awaiting_booking"
    IN_REVIEW = "in_review"
    WAITING_ON_AUTHOR = "waiting_on_author"
    NEEDS_TRIAGE = "needs_triage"
    APPROVED = "approved"
    MERGED = "merged"


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
    # When the MR last moved to `approved`. Null for MRs approved before it was recorded.
    approved_at = models.DateTimeField(null=True, blank=True)
    # When the state last changed. Set by `save()` only (RBW-20).
    state_changed_at = models.DateTimeField(default=timezone.now, db_default=Now())

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project_id", "iid"], name="mr_unique_project_iid"
            )
        ]

    # The state as stored, set when loaded or saved. None on a new instance.
    _loaded_state: str | None = None

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_state = instance.state  # pylint: disable=protected-access
        return instance

    def save(self, *args, **kwargs):
        """Stamp `state_changed_at` when the state differs from the stored one (RBW-20)."""
        if self._loaded_state not in (None, self.state):
            self.state_changed_at = timezone.now()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "state" in update_fields:
                kwargs["update_fields"] = {*update_fields, "state_changed_at"}
        update_fields = kwargs.get("update_fields")
        super().save(*args, **kwargs)
        if update_fields is None or "state" in update_fields:
            self._loaded_state = self.state

    def refresh_from_db(self, using=None, fields=None, from_queryset=None):
        super().refresh_from_db(using, fields, from_queryset)
        if fields is None or "state" in fields:
            self._loaded_state = self.state


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
    APPROVED = "approved"


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
    changes_requested_at = models.DateTimeField(null=True, blank=True)
    # When the area review last became unbooked. Null while it is booked.
    # db_default covers inserts by a worker still on the old code during a deploy.
    unbooked_since = models.DateTimeField(
        null=True, blank=True, default=timezone.now, db_default=Now()
    )


class ReviewSessionOutcome(models.TextChoices):  # pylint: disable=too-many-ancestors
    PENDING = "pending"
    DONE = "done"
    MORE_TIME = "more_time"
    HANDED_BACK = "handed_back"
    SUPERSEDED = "superseded"


class ReviewSession(models.Model):
    """One booking of an area review. The area review caches its latest session (INV-1)."""

    area_review = models.ForeignKey(
        AreaReview,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="sessions",
    )
    reviewer = models.ForeignKey(
        Person, on_delete=models.DO_NOTHING, db_constraint=False, related_name="+"
    )
    day = models.DateField()
    slot_minutes = models.PositiveIntegerField()
    reason = models.TextField(blank=True, default="", db_default="")
    is_continuation = models.BooleanField(default=False, db_default=False)
    outcome = models.CharField(
        max_length=16,
        choices=ReviewSessionOutcome.choices,
        default=ReviewSessionOutcome.PENDING,
        db_default=ReviewSessionOutcome.PENDING,
    )
    outcome_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_default=Now())

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["area_review"],
                condition=models.Q(outcome=ReviewSessionOutcome.PENDING),
                name="session_one_pending",
            )
        ]


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


class MergeRoutingSettings(models.Model):
    """The single row of admin-set merge routing values. Use `load()`."""

    default_daily_merge_limit = models.PositiveIntegerField(default=5, db_default=5)
    merge_window_minutes = models.PositiveIntegerField(
        default=30, db_default=30, validators=[MinValueValidator(1)]
    )
    # Local time (TIME_ZONE) of the daily retry of unassigned approved MRs.
    daily_retry_time = models.TimeField(default=time(8, 0), db_default=time(8, 0))
    # The local day the daily retry last claimed. Written only by the retry.
    last_retry_on = models.DateField(null=True, blank=True, editable=False)

    @classmethod
    def load(cls) -> "MergeRoutingSettings":
        """Return the single settings row, creating it when it is missing."""
        return cls.objects.get_or_create(pk=1)[0]


class RebookingSettings(models.Model):
    """The single row of rebooking and watchdog settings. Use `load()`."""

    # Local time (TIME_ZONE) when the daily rebooking and the watchdog run.
    daily_run_time = models.TimeField(default=time(7, 30), db_default=time(7, 30))
    ai_review_threshold_days = models.PositiveIntegerField(
        default=1,
        db_default=1,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
    )
    triage_threshold_days = models.PositiveIntegerField(
        default=1,
        db_default=1,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
    )
    stale_threshold_days = models.PositiveIntegerField(
        default=5,
        db_default=5,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
    )
    # The local days the jobs last claimed. Written only by the jobs.
    last_rebooking_on = models.DateField(null=True, blank=True, editable=False)
    last_watchdog_on = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Rebooking and watchdog settings"
        verbose_name_plural = "Rebooking and watchdog settings"

    @classmethod
    def load(cls) -> "RebookingSettings":
        """Return the single settings row, creating it when it is missing."""
        return cls.objects.get_or_create(pk=1)[0]


class MergeViolation(models.TextChoices):  # pylint: disable=too-many-ancestors
    NONE = "none"
    AUTHOR = "author"
    APPROVER = "approver"
    BEFORE_APPROVAL = "before_approval"


class MergeAssignment(models.Model):
    """One MR given to a merge owner for a window, and later its recorded merge.

    It is open while it has neither `merged_at` nor `cancelled_at`.
    """

    merge_request = models.ForeignKey(
        MergeRequest,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="merge_assignments",
    )
    merge_owner = models.ForeignKey(
        Person,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="+",
    )
    window = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True, default="", db_default="")
    assigned_at = models.DateTimeField(default=timezone.now, db_default=Now())
    merged_by = models.ForeignKey(
        Person,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        null=True,
        blank=True,
        related_name="+",
    )
    merged_by_gitlab_id = models.BigIntegerField(null=True, blank=True)
    merged_at = models.DateTimeField(null=True, blank=True)
    outside_window = models.BooleanField(default=False, db_default=False)
    by_other_person = models.BooleanField(default=False, db_default=False)
    violation = models.CharField(
        max_length=16,
        choices=MergeViolation.choices,
        default=MergeViolation.NONE,
        db_default=MergeViolation.NONE,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["merge_request"],
                condition=models.Q(merged_at__isnull=True, cancelled_at__isnull=True),
                name="merge_assignment_one_open",
            )
        ]
