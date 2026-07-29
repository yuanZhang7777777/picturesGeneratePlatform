from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Asset,
    AuditEvent,
    Batch,
    Cluster,
    ClusterAsset,
    CompetitorInsight,
    DailyGenerationUsage,
    Generation,
    OutputSlot,
    OutputTemplate,
    PromptNodeTemplate,
    PromptVersion,
    ResultAsset,
    ReviewAnnotation,
    ReviewFeedback,
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


@admin.register(OutputSlot)
class OutputSlotAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.generations.exists():
            return ("template", "order")
        return super().get_readonly_fields(request, obj)


admin.site.register(PromptNodeTemplate)


@admin.register(PromptVersion)
class PromptVersionAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.generations.exists():
            return [field.name for field in self.model._meta.fields]
        return super().get_readonly_fields(request, obj)


admin.site.register(RuleProfile)


@admin.register(Generation)
class GenerationAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        if obj:
            return (
                "batch",
                "cluster",
                "output_slot",
                "prompt_version",
                "created_by",
                "attempt",
                "prompt_text",
                "size",
                "resolution",
                "reference_snapshot",
                "template_snapshot",
                "rule_snapshot",
            )
        return super().get_readonly_fields(request, obj)


admin.site.register(ResultAsset)
admin.site.register(AuditEvent)


@admin.register(DailyGenerationUsage)
class DailyGenerationUsageAdmin(admin.ModelAdmin):
    list_display = ("date", "scope", "user", "used")
    readonly_fields = ("date", "scope", "user", "used")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReviewAnnotationInline(admin.TabularInline):
    model = ReviewAnnotation
    extra = 0
    can_delete = False
    readonly_fields = ("kind", "points", "rect", "color", "width", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ReviewFeedback)
class ReviewFeedbackAdmin(admin.ModelAdmin):
    list_display = ("generation", "reviewer", "decision", "created_at")
    readonly_fields = (
        "generation",
        "reviewer",
        "decision",
        "issue_tags",
        "description",
        "created_at",
    )
    inlines = (ReviewAnnotationInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReviewAnnotation)
class ReviewAnnotationAdmin(admin.ModelAdmin):
    readonly_fields = ("feedback", "kind", "points", "rect", "color", "width", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
