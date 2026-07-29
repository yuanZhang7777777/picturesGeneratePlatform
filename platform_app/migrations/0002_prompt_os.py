import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("platform_app", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="batch",
            name="market",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="batch",
            name="output_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="batches",
                to="platform_app.outputtemplate",
            ),
        ),
        migrations.AddField(
            model_name="batch",
            name="resolution",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="batch",
            name="rule_profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="batches",
                to="platform_app.ruleprofile",
            ),
        ),
        migrations.AddField(
            model_name="batch",
            name="size",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="cluster",
            name="target_consumer",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="generation",
            name="rule_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="generation",
            name="template_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="outputtemplate",
            name="version",
            field=models.CharField(default="v1", max_length=40),
        ),
        migrations.AddField(
            model_name="promptversion",
            name="evaluation",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="promptversion",
            name="input_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="promptversion",
            name="model",
            field=models.CharField(default="gpt-image-2", max_length=80),
        ),
        migrations.AddField(
            model_name="promptversion",
            name="node_name",
            field=models.CharField(default="slot_prompt", max_length=80),
        ),
        migrations.AddField(
            model_name="promptversion",
            name="template_version",
            field=models.CharField(default="builtin-v1", max_length=40),
        ),
        migrations.CreateModel(
            name="PromptNodeTemplate",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("node_name", models.CharField(max_length=80)),
                ("version", models.CharField(default="v1", max_length=40)),
                (
                    "status",
                    models.CharField(
                        choices=[("draft", "Draft"), ("published", "Published"), ("retired", "Retired")],
                        default="draft",
                        max_length=20,
                    ),
                ),
                ("instruction", models.TextField()),
                ("output_schema", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="CompetitorInsight",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("style_dna", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "cluster",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="competitor_insights",
                        to="platform_app.cluster",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="promptnodetemplate",
            constraint=models.UniqueConstraint(
                fields=("node_name", "version"),
                name="unique_prompt_node_version",
            ),
        ),
    ]
