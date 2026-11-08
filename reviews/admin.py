from django import forms
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from integrations.admin import ReadOnlyAdmin
from reviews import merge_retry, rebooking
from reviews.models import (
    AreaReview,
    CapacityDay,
    CodeArea,
    Leave,
    MergeAssignment,
    MergeRequest,
    MergeRoutingSettings,
    Ownership,
    Person,
    RebookingSettings,
)


class PersonAdminForm(forms.ModelForm):
    """Edit the merge times as comma-separated text, such as `10:00, 15:30`."""

    merge_times = forms.CharField(required=False, help_text="HH:MM, comma-separated")

    class Meta:
        model = Person
        fields = "__all__"

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.initial["merge_times"] = ", ".join(self.instance.merge_times)

    def clean_merge_times(self) -> list[str]:
        text = self.cleaned_data["merge_times"]
        return [entry.strip() for entry in text.split(",") if entry.strip()]


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    """Person admin. `user` links a Django account to the developer panel."""

    form = PersonAdminForm

    fields = [
        "gitlab_user_id",
        "name",
        "working_days",
        "daily_budget_minutes",
        "is_admin",
        "is_merge_owner",
        "merge_times",
        "daily_merge_limit",
        "user",
    ]
    raw_id_fields = ["user"]


@admin.register(MergeRoutingSettings)
class MergeRoutingSettingsAdmin(admin.ModelAdmin):
    """Edit the single settings row: no add, no delete."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: MergeRoutingSettings | None = None
    ) -> bool:
        return False

    def save_model(self, request, obj, form, change) -> None:
        """Save only the editable fields, so a day claim the worker wrote since
        the form was loaded is never written back to an older value (INV-1)."""
        obj.save(
            update_fields=[
                field.name
                for field in obj._meta.concrete_fields  # pylint: disable=protected-access
                if field.editable and not field.primary_key
            ]
        )


@admin.register(RebookingSettings)
class RebookingSettingsAdmin(MergeRoutingSettingsAdmin):
    """Edit the single settings row: no add, no delete."""


for model in (Leave, CodeArea, Ownership):
    admin.site.register(model)


@admin.register(MergeRequest)
class MergeRequestAdmin(ReadOnlyAdmin):
    """Read-only, apart from the "Retry merge routing" (MRDR-4) and "Retry booking" actions."""

    actions = ["retry_merge_routing", "retry_booking"]

    @admin.action(description="Retry merge routing", permissions=["change"])
    def retry_merge_routing(
        self, request: HttpRequest, queryset: QuerySet[MergeRequest]
    ) -> None:
        result = merge_retry.retry_unassigned(
            merge_retry.ADMIN, list(queryset.values_list("pk", flat=True))
        )
        self.message_user(
            request,
            f"{result.assigned} assigned, {len(result.unassigned)} still unassigned, "
            f"{result.skipped} skipped.",
            messages.SUCCESS,
        )

    @admin.action(description="Retry booking", permissions=["change"])
    def retry_booking(
        self, request: HttpRequest, queryset: QuerySet[MergeRequest]
    ) -> None:
        result = rebooking.retry_unbooked(
            rebooking.ADMIN, list(queryset.values_list("pk", flat=True))
        )
        self.message_user(
            request,
            f"{result.booked} booked, {len(result.unbooked)} still unbooked, "
            f"{result.skipped} skipped.",
            messages.SUCCESS,
        )


for model in (AreaReview, CapacityDay, MergeAssignment):
    admin.site.register(model, ReadOnlyAdmin)
