from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("platform_app", "0014_cluster_configuration_overrides"),
    ]

    operations = [
        migrations.AlterField(
            model_name="batch",
            name="platform",
            field=models.CharField(default="generic", max_length=40),
        ),
        migrations.AlterField(
            model_name="batch",
            name="site",
            field=models.CharField(default="SEA", max_length=40),
        ),
        migrations.AlterField(
            model_name="batch",
            name="market",
            field=models.CharField(blank=True, default="SEA", max_length=40),
        ),
        migrations.AlterField(
            model_name="batch",
            name="size",
            field=models.CharField(blank=True, default="1:1", max_length=20),
        ),
        migrations.AlterField(
            model_name="batch",
            name="resolution",
            field=models.CharField(blank=True, default="1k", max_length=20),
        ),
        migrations.AlterField(
            model_name="cluster",
            name="preparation_status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("pending", "Pending"),
                    ("preparing", "Preparing"),
                    ("ready", "Ready"),
                    ("blocked", "Blocked"),
                    ("failed", "Failed"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="cluster",
            name="preparation_stage",
            field=models.CharField(default="draft", max_length=20),
        ),
        migrations.AddField(
            model_name="cluster",
            name="preparation_current",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="cluster",
            name="preparation_total",
            field=models.PositiveSmallIntegerField(default=7),
        ),
        migrations.AddField(
            model_name="promptnodetemplate",
            name="user_message_template",
            field=models.TextField(blank=True),
        ),
    ]
