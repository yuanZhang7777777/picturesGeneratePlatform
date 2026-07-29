from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Asset,
    AuditEvent,
    Batch,
    Cluster,
    ClusterAsset,
    CompetitorInsight,
    Generation,
    OutputSlot,
    OutputTemplate,
    PromptNodeTemplate,
    PromptVersion,
    ResultAsset,
    RuleProfile,
    User,
)


@admin.register(User)
class PlatformUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("Platform", {"fields": ("role", "daily_generation_limit", "must_change_password")}),
    )
    list_display = ("username", "role", "is_staff", "is_active")


admin.site.register(Batch)
admin.site.register(Asset)
admin.site.register(Cluster)
admin.site.register(ClusterAsset)
admin.site.register(CompetitorInsight)
admin.site.register(OutputTemplate)
admin.site.register(OutputSlot)
admin.site.register(PromptNodeTemplate)
admin.site.register(PromptVersion)
admin.site.register(RuleProfile)
admin.site.register(Generation)
admin.site.register(ResultAsset)
admin.site.register(AuditEvent)
