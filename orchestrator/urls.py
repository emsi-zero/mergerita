from django.contrib import admin
from django.urls import path

from reviews.views import GitLabWebhookView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("webhooks/gitlab/", GitLabWebhookView.as_view(), name="gitlab-webhook"),
]
