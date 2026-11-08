import datetime

import django.core.validators
import django.db.models.functions.datetime
import django.utils.timezone
from django.db import migrations, models
from django.db.models import CharField, F, OuterRef, Subquery
from django.db.models.functions import Cast, Coalesce


def backfill(apps, schema_editor):
    """Fill `state_changed_at` and `unbooked_since` from the audit log (RBW-21)."""
    audit_event = apps.get_model("audit", "AuditEvent")
    merge_request = apps.get_model("reviews", "MergeRequest")
    area_review = apps.get_model("reviews", "AreaReview")

    latest_state_change = (
        audit_event.objects.filter(
            subject_type="reviews.MergeRequest",
            subject_id=Cast(OuterRef("pk"), CharField()),
            after__has_key="state",
        )
        .order_by("-created_at")
        .values("created_at")[:1]
    )
    merge_request.objects.update(
        state_changed_at=Coalesce(Subquery(latest_state_change), F("opened_at"))
    )

    latest_unbooked = (
        audit_event.objects.filter(
            subject_type="reviews.AreaReview",
            subject_id=Cast(OuterRef("pk"), CharField()),
            action__in=["area_review.unbooked", "area_review.created"],
        )
        .order_by("-created_at")
        .values("created_at")[:1]
    )
    opened_at = merge_request.objects.filter(pk=OuterRef("merge_request_id")).values(
        "opened_at"
    )[:1]
    # The new column defaults to now for every row; only unbooked rows keep a time.
    area_review.objects.exclude(state="unbooked").update(unbooked_since=None)
    area_review.objects.filter(state="unbooked").update(
        unbooked_since=Coalesce(Subquery(latest_unbooked), Subquery(opened_at))
    )
    # Create the settings row, so the admin can set it before the worker first runs.
    apps.get_model("reviews", "RebookingSettings").objects.get_or_create(pk=1)


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_subject_index"),
        ("reviews", "0012_merge_retry"),
    ]

    operations = [
        migrations.CreateModel(
            name="RebookingSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "daily_run_time",
                    models.TimeField(
                        db_default=datetime.time(7, 30), default=datetime.time(7, 30)
                    ),
                ),
                (
                    "ai_review_threshold_days",
                    models.PositiveIntegerField(
                        db_default=1,
                        default=1,
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(60),
                        ],
                    ),
                ),
                (
                    "triage_threshold_days",
                    models.PositiveIntegerField(
                        db_default=1,
                        default=1,
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(60),
                        ],
                    ),
                ),
                (
                    "stale_threshold_days",
                    models.PositiveIntegerField(
                        db_default=5,
                        default=5,
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(60),
                        ],
                    ),
                ),
                (
                    "last_rebooking_on",
                    models.DateField(blank=True, editable=False, null=True),
                ),
                (
                    "last_watchdog_on",
                    models.DateField(blank=True, editable=False, null=True),
                ),
            ],
            options={
                "verbose_name": "Rebooking and watchdog settings",
                "verbose_name_plural": "Rebooking and watchdog settings",
            },
        ),
        # MergeRequest before AreaReview: the worker locks rows in that order (LOCK-1).
        migrations.AddField(
            model_name="mergerequest",
            name="state_changed_at",
            field=models.DateTimeField(
                db_default=django.db.models.functions.datetime.Now(),
                default=django.utils.timezone.now,
            ),
        ),
        migrations.AddField(
            model_name="areareview",
            name="unbooked_since",
            field=models.DateTimeField(
                blank=True,
                db_default=django.db.models.functions.datetime.Now(),
                default=django.utils.timezone.now,
                null=True,
            ),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
