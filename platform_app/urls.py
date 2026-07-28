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
    path("api/batches/<uuid:batch_id>/assets/", views.api_upload_assets, name="api_upload_assets"),
    path("api/batches/<uuid:batch_id>/snapshot/", views.api_batch_snapshot, name="api_batch_snapshot"),
    path("api/batches/<uuid:batch_id>/preflight/", views.api_preflight, name="api_preflight"),
    path("api/batches/<uuid:batch_id>/confirm/", views.api_confirm_generation, name="api_confirm_generation"),
    path("api/clusters/<uuid:cluster_id>/", views.api_update_cluster, name="api_update_cluster"),
    path("api/clusters/<uuid:cluster_id>/merge/", views.api_merge_asset, name="api_merge_asset"),
    path("api/assets/<uuid:asset_id>/split/", views.api_split_asset, name="api_split_asset"),
    path("api/generations/<uuid:generation_id>/retry/", views.api_generation_retry, name="api_generation_retry"),
    path("health/live", views.health_live, name="health_live"),
]
