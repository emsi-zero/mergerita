from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path

from reviews.views import GitLabWebhookView, MergeRequestDetailView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", LoginView.as_view(), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("panel/", include("panel.urls")),
    path("webhooks/gitlab/", GitLabWebhookView.as_view(), name="gitlab-webhook"),
    path(
        "api/merge-requests/<int:pk>/",
        MergeRequestDetailView.as_view(),
        name="merge-request-detail",
    ),
]
