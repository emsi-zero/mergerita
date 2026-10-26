from django.contrib import admin
from django.http import HttpRequest

from integrations.models import InboxEvent, OutboxCall


class ReadOnlyAdmin(admin.ModelAdmin):
    """Show rows only: no add, no delete, every field read-only."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj=None) -> bool:
        return False

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> list[str]:
        return [field.name for field in self.model._meta.fields]


@admin.register(InboxEvent)
class InboxEventAdmin(ReadOnlyAdmin):
    """Only the failed mark and its error are editable, to clear a failed event."""

    def get_readonly_fields(self, request: HttpRequest, obj=None) -> list[str]:
        return [
            name
            for name in super().get_readonly_fields(request, obj)
            if name not in ("failed_at", "last_error")
        ]


admin.site.register(OutboxCall, ReadOnlyAdmin)
