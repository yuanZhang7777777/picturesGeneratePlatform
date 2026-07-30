from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django import forms

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


class PlatformAdminOnlyMixin:
    def _allowed(self, request):
        return bool(
            request.user
            and request.user.is_active
            and request.user.is_staff
            and request.user.is_platform_admin
        )

    def has_module_permission(self, request):
        return self._allowed(request)

    def has_view_permission(self, request, obj=None):
        return self._allowed(request)

    def has_add_permission(self, request):
        return self._allowed(request)

    def has_change_permission(self, request, obj=None):
        return self._allowed(request)

    def has_delete_permission(self, request, obj=None):
        return self._allowed(request)


class RuleProfileAdminForm(forms.ModelForm):
    class Meta:
        model = RuleProfile
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("status") == RuleProfile.Status.PUBLISHED and cleaned.get("platform") != "global":
            for field in ("source_url", "checked_at", "site", "version"):
                if not cleaned.get(field):
                    self.add_error(field, "Published market rules require official source metadata.")
        return cleaned


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
@admin.register(OutputTemplate)
class OutputTemplateAdmin(PlatformAdminOnlyMixin, admin.ModelAdmin):
    list_display = ("name", "platform", "site", "version", "status")


@admin.register(OutputSlot)
class OutputSlotAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.generations.exists():
            return ("template", "order")
        return super().get_readonly_fields(request, obj)


@admin.register(PromptNodeTemplate)
class PromptNodeTemplateAdmin(PlatformAdminOnlyMixin, admin.ModelAdmin):
    list_display = ("node_name", "version", "status", "updated_at")


@admin.register(PromptVersion)
class PromptVersionAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        if obj and obj.generations.exists():
            return [field.name for field in self.model._meta.fields]
        return super().get_readonly_fields(request, obj)


@admin.register(RuleProfile)
class RuleProfileAdmin(PlatformAdminOnlyMixin, admin.ModelAdmin):
    form = RuleProfileAdminForm
    list_display = ("name", "platform", "site", "version", "status", "checked_at")


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

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


admin.site.register(ResultAsset)
admin.site.register(AuditEvent)


@admin.register(DailyGenerationUsage)
class DailyGenerationUsageAdmin(PlatformAdminOnlyMixin, admin.ModelAdmin):
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
