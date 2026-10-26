from django.contrib import admin
from django.urls import path

from reviews.views import GitLabWebhookView, MergeRequestDetailView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("webhooks/gitlab/", GitLabWebhookView.as_view(), name="gitlab-webhook"),
    path(
        "api/merge-requests/<int:pk>/",
        MergeRequestDetailView.as_view(),
        name="merge-request-detail",
    ),
]
