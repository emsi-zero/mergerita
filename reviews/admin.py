from django.contrib import admin

from integrations.admin import ReadOnlyAdmin
from reviews.models import (
    AreaReview,
    CapacityDay,
    CodeArea,
    Leave,
    MergeRequest,
    Ownership,
    Person,
)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    """Person admin. `user` links a Django account to the developer panel."""

    fields = [
        "gitlab_user_id",
        "name",
        "working_days",
        "daily_budget_minutes",
        "is_admin",
        "user",
    ]
    raw_id_fields = ["user"]


for model in (Leave, CodeArea, Ownership):
    admin.site.register(model)
for model in (MergeRequest, AreaReview, CapacityDay):
    admin.site.register(model, ReadOnlyAdmin)
