from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views


urlpatterns = [
    path("login/", views.PlatformLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("password/change/", views.password_change, name="password_change"),
    path("batches/", views.legacy_batch_list_redirect, name="legacy_batch_list_redirect"),
    path("batches/new/", views.legacy_batch_new_redirect, name="legacy_batch_new_redirect"),
    path("batches/<uuid:batch_id>/", views.legacy_batch_detail_redirect, name="legacy_batch_detail_redirect"),
    path("api/csrf/", views.api_csrf, name="api_csrf"),
    path("api/workspace/snapshot/", views.api_workspace_snapshot, name="api_workspace_snapshot"),
    path("api/projects/", views.api_project_create, name="api_project_create"),
    path(
        "api/projects/<uuid:batch_id>/snapshot/",
        views.api_project_snapshot,
        name="api_project_snapshot",
    ),
    path(
        "api/projects/<uuid:batch_id>/settings/",
        views.api_project_settings,
        name="api_project_settings",
    ),
    path(
        "api/projects/<uuid:batch_id>/prepare/",
        views.api_project_prepare,
        name="api_project_prepare",
    ),
    path(
        "api/projects/<uuid:batch_id>/pause/",
        views.api_project_pause,
        name="api_project_pause",
    ),
    path(
        "api/admin/prompt-nodes/",
        views.api_admin_prompt_nodes,
        name="api_admin_prompt_nodes",
    ),
    path(
        "api/admin/prompt-nodes/publish/",
        views.api_admin_prompt_nodes_publish,
        name="api_admin_prompt_nodes_publish",
    ),
    path(
        "api/projects/<uuid:batch_id>/assets/",
        views.api_upload_assets,
        name="api_project_upload_assets",
    ),
    path(
        "api/projects/<uuid:batch_id>/sku-import/",
        views.api_sku_import,
        name="api_sku_import",
    ),
    path(
        "api/projects/<uuid:batch_id>/preflight/",
        views.api_preflight,
        name="api_project_preflight",
    ),
    path(
        "api/projects/<uuid:batch_id>/confirm/",
        views.api_confirm_generation,
        name="api_project_confirm",
    ),
    path(
        "api/projects/<uuid:batch_id>/generate/",
        views.api_project_generate,
        name="api_project_generate",
    ),
    path(
        "api/projects/<uuid:batch_id>/export/",
        views.api_project_export,
        name="api_project_export",
    ),
    path("api/batches/<uuid:batch_id>/assets/", views.api_upload_assets, name="api_upload_assets"),
    path("api/batches/<uuid:batch_id>/snapshot/", views.api_batch_snapshot, name="api_batch_snapshot"),
    path("api/batches/<uuid:batch_id>/preflight/", views.api_preflight, name="api_preflight"),
    path("api/batches/<uuid:batch_id>/confirm/", views.api_confirm_generation, name="api_confirm_generation"),
    path("api/batches/<uuid:batch_id>/generate/", views.api_project_generate, name="api_generate_batch"),
    path("api/clusters/<uuid:cluster_id>/", views.api_update_cluster, name="api_update_cluster"),
    path("api/clusters/<uuid:cluster_id>/optimize-prompt/", views.api_optimize_prompt, name="api_optimize_prompt"),
    path("api/clusters/<uuid:cluster_id>/merge/", views.api_merge_asset, name="api_merge_asset"),
    path("api/assets/<uuid:asset_id>/", views.api_delete_asset, name="api_delete_asset"),
    path("api/assets/<uuid:asset_id>/split/", views.api_split_asset, name="api_split_asset"),
    path("api/assets/<uuid:asset_id>/media/", views.api_asset_media, name="api_asset_media"),
    path("api/results/<uuid:result_id>/media/", views.api_result_media, name="api_result_media"),
    path("api/generations/<uuid:generation_id>/retry/", views.api_generation_retry, name="api_generation_retry"),
    path(
        "api/generations/<uuid:generation_id>/regenerate/",
        views.api_generation_regenerate,
        name="api_generation_regenerate",
    ),
    path(
        "api/generations/<uuid:generation_id>/revise/",
        views.api_generation_revise,
        name="api_generation_revise",
    ),
    path(
        "api/generations/<uuid:generation_id>/review/",
        views.api_generation_review,
        name="api_generation_review",
    ),
    path("health/live", views.health_live, name="health_live"),
    path("health/ready", views.health_ready, name="health_ready"),
]
