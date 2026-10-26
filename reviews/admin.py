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

for model in (Person, Leave, CodeArea, Ownership):
    admin.site.register(model)
for model in (MergeRequest, AreaReview, CapacityDay):
    admin.site.register(model, ReadOnlyAdmin)
