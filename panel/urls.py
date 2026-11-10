from django.urls import path

from panel import auth_views, views

app_name = "panel"

urlpatterns = [
    path("login/", auth_views.PanelLoginView.as_view(), name="login"),
    path("logout/", auth_views.PanelLogoutView.as_view(), name="logout"),
    path("sso/start/", auth_views.SsoStartView.as_view(), name="sso-start"),
    path("sso/callback/", auth_views.SsoCallbackView.as_view(), name="sso-callback"),
    path("", views.PanelHomeView.as_view(), name="home"),
    path("mrs/", views.MyMergeRequestsView.as_view(), name="mrs"),
    path(
        "mrs/<int:pk>/",
        views.MergeRequestDetailPanelView.as_view(),
        name="mr-detail",
    ),
    path(
        "mrs/<int:pk>/urgency/",
        views.RequestUrgencyView.as_view(),
        name="urgency-request",
    ),
    path(
        "mrs/<int:pk>/urgency/withdraw/",
        views.WithdrawUrgencyView.as_view(),
        name="urgency-withdraw",
    ),
    path("urgency/", views.UrgencyRequestsView.as_view(), name="urgency"),
    path(
        "urgency/<int:pk>/approve/",
        views.ApproveUrgencyView.as_view(),
        name="urgency-approve",
    ),
    path(
        "urgency/<int:pk>/decline/",
        views.DeclineUrgencyView.as_view(),
        name="urgency-decline",
    ),
    path("reviews/", views.MyReviewsView.as_view(), name="reviews"),
    path(
        "reviews/<int:pk>/presence/",
        views.RequestPresenceView.as_view(),
        name="presence-request",
    ),
    path(
        "reviews/<int:pk>/presence/cancel/",
        views.CancelPresenceView.as_view(),
        name="presence-cancel",
    ),
    path(
        "sessions/<int:pk>/done/",
        views.SessionDoneView.as_view(),
        name="session-done",
    ),
    path(
        "sessions/<int:pk>/more-time/",
        views.SessionMoreTimeView.as_view(),
        name="session-more-time",
    ),
    path(
        "sessions/<int:pk>/hand-back/",
        views.SessionHandBackView.as_view(),
        name="session-hand-back",
    ),
    path("merges/", views.MyMergesView.as_view(), name="merges"),
    path("availability/", views.AvailabilityView.as_view(), name="availability"),
    path(
        "availability/days/", views.WorkingDaysView.as_view(), name="availability-days"
    ),
    path(
        "availability/budget/", views.BudgetView.as_view(), name="availability-budget"
    ),
    path(
        "availability/leave/", views.AddLeaveView.as_view(), name="availability-leave"
    ),
    path(
        "availability/leave/<int:pk>/delete/",
        views.DeleteLeaveView.as_view(),
        name="availability-leave-delete",
    ),
]
