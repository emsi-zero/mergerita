from django.urls import path

from panel import views

app_name = "panel"

urlpatterns = [
    path("", views.PanelHomeView.as_view(), name="home"),
    path("mrs/", views.MyMergeRequestsView.as_view(), name="mrs"),
    path(
        "mrs/<int:pk>/",
        views.MergeRequestDetailPanelView.as_view(),
        name="mr-detail",
    ),
    path("reviews/", views.MyReviewsView.as_view(), name="reviews"),
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
