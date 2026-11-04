from django import forms
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from integrations.admin import ReadOnlyAdmin
from reviews import merge_retry
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


for model in (Leave, CodeArea, Ownership):
    admin.site.register(model)


@admin.register(MergeRequest)
class MergeRequestAdmin(ReadOnlyAdmin):
    """Read-only, apart from the "Retry merge routing" action (MRDR-4)."""

    actions = ["retry_merge_routing"]

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


for model in (AreaReview, CapacityDay, MergeAssignment):
    admin.site.register(model, ReadOnlyAdmin)
