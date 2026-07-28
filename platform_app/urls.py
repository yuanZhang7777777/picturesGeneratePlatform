from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views


urlpatterns = [
    path("", views.batch_list, name="batch_list"),
    path("login/", views.PlatformLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("password/change/", views.password_change, name="password_change"),
    path("batches/", views.batch_list, name="batch_list"),
    path("batches/new/", views.batch_new, name="batch_new"),
    path("batches/<uuid:batch_id>/", views.batch_detail, name="batch_detail"),
    path("health/live", views.health_live, name="health_live"),
]
