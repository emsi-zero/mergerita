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
]
