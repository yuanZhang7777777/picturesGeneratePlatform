import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max
from django.utils import timezone


class ImmutableReviewQuerySet(models.QuerySet):
    def delete(self, *args, **kwargs):
        raise ValidationError("Review records are immutable")


class ProtectedOutputSlotQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if {"order", "template", "template_id"} & kwargs.keys() and self.filter(generations__isnull=False).exists():
            raise ValidationError("OutputSlot order and template are immutable after generation")
        return super().update(**kwargs)


class User(AbstractUser):
    class Role(models.TextChoices):
        OPERATOR = "operator", "Operator"
        ADMIN = "admin", "Admin"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.OPERATOR)
    daily_generation_limit = models.PositiveIntegerField(default=settings.USER_DAILY_GENERATION_LIMIT)
    must_change_password = models.BooleanField(default=True)

    @property
    def is_platform_admin(self):
        return self.is_superuser or self.role == self.Role.ADMIN


class Batch(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UPLOADING = "uploading", "Uploading"
        ORGANIZING = "organizing", "Organizing"
        READY = "ready", "Ready"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Partial"
        NEEDS_INPUT = "needs_input", "Needs input"
        FAILED = "failed", "Failed"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="batches")
    name = models.CharField(max_length=200)
    platform = models.CharField(max_length=40, default="shopee")
    site = models.CharField(max_length=40, default="SG")
    market = models.CharField(max_length=40, blank=True)
    output_template = models.ForeignKey(
        "OutputTemplate",
        on_delete=models.PROTECT,
        related_name="batches",
        null=True,
        blank=True,
    )
    rule_profile = models.ForeignKey(
        "RuleProfile",
        on_delete=models.PROTECT,
        related_name="batches",
        null=True,
        blank=True,
    )
    size = models.CharField(max_length=20, blank=True)
    resolution = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    global_prompt = models.TextField(blank=True)
    confirmed_generation_key = models.UUIDField(null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def recompute_status(self):
        generations = list(self.generations.all())
        if not generations:
            return self.status
        statuses = {generation.status for generation in generations}
        if statuses == {Generation.Status.COMPLETED}:
            self.status = self.Status.COMPLETED
        elif Generation.Status.FAILED in statuses and any(
            status == Generation.Status.COMPLETED for status in statuses
        ):
            self.status = self.Status.PARTIAL
        elif statuses <= {Generation.Status.FAILED}:
            self.status = self.Status.FAILED
        else:
            self.status = self.Status.RUNNING
        self.save(update_fields=["status", "updated_at"])
        return self.status

    def __str__(self):
        return self.name


class Asset(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        TXT = "txt", "Text"

    class ValidationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        VALID = "valid", "Valid"
        INVALID = "invalid", "Invalid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="assets")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    original_filename = models.CharField(max_length=255)
    storage_path = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64)
    file_size = models.PositiveIntegerField()
    content_type = models.CharField(max_length=100)
    validation_status = models.CharField(
        max_length=20,
        choices=ValidationStatus.choices,
        default=ValidationStatus.VALID,
    )
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    text_content = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.original_filename


class Cluster(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="clusters")
    name = models.CharField(max_length=200)
    product_name = models.CharField(max_length=200, blank=True)
    product_facts = models.TextField(blank=True)
    identity_lock = models.TextField(blank=True)
    target_consumer = models.CharField(max_length=40, blank=True)
    prompt_override = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    assets = models.ManyToManyField(Asset, through="ClusterAsset", related_name="clusters")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def create_for_asset(cls, batch, asset):
        cluster = cls.objects.create(batch=batch, name=asset.original_filename)
        ClusterAsset.objects.create(cluster=cluster, asset=asset, role=ClusterAsset.Role.PRIMARY, order=1)
        return cluster

    def add_asset(self, asset):
        if asset.kind != Asset.Kind.IMAGE:
            raise ValueError("Only image assets can be added to a cluster")
        if asset.batch_id != self.batch_id:
            raise ValueError("Asset belongs to a different batch")
        if self.cluster_assets.count() >= 16:
            raise ValueError("A cluster can contain at most 16 reference images")
        next_order = (self.cluster_assets.aggregate(value=Max("order"))["value"] or 0) + 1
        role = ClusterAsset.Role.REFERENCE if self.cluster_assets.exists() else ClusterAsset.Role.PRIMARY
        cluster_asset, _ = ClusterAsset.objects.update_or_create(
            asset=asset,
            defaults={"cluster": self, "role": role, "order": next_order},
        )
        self.version += 1
        self.save(update_fields=["version", "updated_at"])
        return cluster_asset

    def __str__(self):
        return self.name


class ClusterAsset(models.Model):
    class Role(models.TextChoices):
        PRIMARY = "primary", "Primary"
        REFERENCE = "reference", "Reference"

    cluster = models.ForeignKey(Cluster, on_delete=models.CASCADE, related_name="cluster_assets")
    asset = models.OneToOneField(Asset, on_delete=models.CASCADE, related_name="cluster_asset")
    role = models.CharField(max_length=20, choices=Role.choices)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["cluster", "order"], name="unique_cluster_asset_order"),
        ]


class RuleProfile(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        RETIRED = "retired", "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seed_key = models.CharField(max_length=120, unique=True, null=True, blank=True)
    platform = models.CharField(max_length=40)
    site = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    version = models.CharField(max_length=40, default="v1")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    source_url = models.URLField(blank=True)
    checked_at = models.DateField(null=True, blank=True)
    rules = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.platform}/{self.site} {self.name}"


class OutputTemplate(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        RETIRED = "retired", "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seed_key = models.CharField(max_length=120, unique=True, null=True, blank=True)
    platform = models.CharField(max_length=40)
    site = models.CharField(max_length=40, blank=True)
    name = models.CharField(max_length=120)
    version = models.CharField(max_length=40, default="v1")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PUBLISHED)
    default_size = models.CharField(max_length=20, default="1:1")
    default_resolution = models.CharField(max_length=20, default="1k")

    def __str__(self):
        return self.name


class OutputSlot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    template = models.ForeignKey(OutputTemplate, on_delete=models.CASCADE, related_name="slots")
    name = models.CharField(max_length=120)
    order = models.PositiveIntegerField()
    purpose = models.TextField(blank=True)
    objects = ProtectedOutputSlotQuerySet.as_manager()

    class Meta:
        ordering = ["order", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(order__gte=1), name="output_slot_order_gte_one"),
            models.UniqueConstraint(fields=["template", "order"], name="unique_template_slot_order"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self._state.adding:
            current = type(self).objects.filter(pk=self.pk).values("template_id", "order").first()
            if current and self.generations.exists() and (
                current["template_id"] != self.template_id or current["order"] != self.order
            ):
                raise ValidationError("OutputSlot order and template are immutable after generation")
        return super().save(*args, **kwargs)


class PromptNodeTemplate(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        RETIRED = "retired", "Retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    node_name = models.CharField(max_length=80)
    version = models.CharField(max_length=40, default="v1")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    instruction = models.TextField()
    output_schema = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["node_name", "version"], name="unique_prompt_node_version"),
        ]

    def __str__(self):
        return f"{self.node_name}/{self.version}"


class CompetitorInsight(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cluster = models.ForeignKey(Cluster, on_delete=models.CASCADE, related_name="competitor_insights")
    style_dna = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PromptVersion(models.Model):
    IMMUTABLE_SNAPSHOT_FIELDS = (
        "cluster_id",
        "created_by_id",
        "node_name",
        "template_version",
        "provider_model",
        "prompt_text",
        "input_snapshot",
        "structured_output",
        "evaluation",
        "source_snapshot",
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cluster = models.ForeignKey(Cluster, on_delete=models.CASCADE, related_name="prompt_versions")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    node_name = models.CharField(max_length=80, default="slot_prompt")
    template_version = models.CharField(max_length=40, default="builtin-v1")
    provider_model = models.CharField(max_length=80, default="gpt-image-2")
    prompt_text = models.TextField()
    input_snapshot = models.JSONField(default=dict, blank=True)
    structured_output = models.JSONField(default=dict, blank=True)
    evaluation = models.JSONField(default=dict, blank=True)
    source_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self._state.adding and self.generations.exists():
            current = type(self).objects.filter(pk=self.pk).values(*self.IMMUTABLE_SNAPSHOT_FIELDS).get()
            if any(current[field] != getattr(self, field) for field in self.IMMUTABLE_SNAPSHOT_FIELDS):
                raise ValidationError("PromptVersion snapshots are immutable after use by a generation")
        return super().save(*args, **kwargs)


class Generation(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PREPARING = "preparing", "Preparing"
        SUBMITTING = "submitting", "Submitting"
        SUBMITTED = "submitted", "Submitted"
        PROCESSING = "processing", "Processing"
        ARCHIVING = "archiving", "Archiving"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        SUBMIT_UNKNOWN = "submit_unknown", "Submit unknown"
        CANCELED = "canceled", "Canceled"

    class ReviewStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name="generations")
    cluster = models.ForeignKey(Cluster, on_delete=models.CASCADE, related_name="generations")
    output_slot = models.ForeignKey(OutputSlot, on_delete=models.PROTECT, related_name="generations")
    prompt_version = models.ForeignKey(
        PromptVersion,
        on_delete=models.PROTECT,
        related_name="generations",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_generations",
        null=True,
        blank=True,
    )
    attempt = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    review_status = models.CharField(
        max_length=20,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )
    prompt_text = models.TextField(blank=True)
    size = models.CharField(max_length=20, default="1:1")
    resolution = models.CharField(max_length=20, default="1k")
    provider_task_id = models.CharField(max_length=120, blank=True, null=True, unique=True)
    provider_payload = models.JSONField(default=dict, blank=True)
    reference_snapshot = models.JSONField(default=list, blank=True)
    template_snapshot = models.JSONField(default=dict, blank=True)
    rule_snapshot = models.JSONField(default=dict, blank=True)
    failure_reason = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["cluster", "output_slot", "attempt"],
                name="unique_generation_attempt",
            ),
        ]

    def retry_failed(self, user):
        from .services import retry_failed_generation

        return retry_failed_generation(self, user)


class ResultAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    generation = models.ForeignKey(Generation, on_delete=models.CASCADE, related_name="result_assets")
    storage_path = models.CharField(max_length=500)
    source_url = models.URLField(blank=True)
    sha256 = models.CharField(max_length=64)
    file_size = models.PositiveIntegerField()
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["storage_path"], name="unique_result_storage_path"),
        ]


class ReviewFeedback(models.Model):
    class Decision(models.TextChoices):
        ACCEPT = "accept", "Accept"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    generation = models.OneToOneField(
        Generation,
        on_delete=models.PROTECT,
        related_name="review_feedback",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="review_feedback",
    )
    decision = models.CharField(max_length=20, choices=Decision.choices)
    issue_tags = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableReviewQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Review feedback is immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Review feedback is immutable")


class ReviewAnnotation(models.Model):
    class Kind(models.TextChoices):
        STROKE = "stroke", "Stroke"
        CIRCLE = "circle", "Circle"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    feedback = models.ForeignKey(
        ReviewFeedback,
        on_delete=models.PROTECT,
        related_name="annotations",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    points = models.JSONField(default=list, blank=True)
    rect = models.JSONField(default=list, blank=True)
    color = models.CharField(max_length=32, default="#ff0000")
    width = models.FloatField(default=2)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ImmutableReviewQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Review annotation is immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Review annotation is immutable")


class DailyGenerationUsage(models.Model):
    class Scope(models.TextChoices):
        ORGANIZATION = "org", "Organization"
        USER = "user", "User"

    scope = models.CharField(max_length=10, choices=Scope.choices)
    date = models.DateField(default=timezone.localdate)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="daily_generation_usage",
        null=True,
        blank=True,
    )
    used = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(scope="org", user__isnull=True)
                    | models.Q(scope="user", user__isnull=False)
                ),
                name="valid_daily_generation_usage_scope",
            ),
            models.UniqueConstraint(
                fields=["date"],
                condition=models.Q(scope="org"),
                name="unique_daily_org_generation_usage",
            ),
            models.UniqueConstraint(
                fields=["date", "user"],
                condition=models.Q(scope="user"),
                name="unique_daily_user_generation_usage",
            ),
        ]


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
