from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("platform_app", "0013_asset_cluster_archived_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="cluster",
            name="market_override",
            field=models.CharField(blank=True, max_length=40, null=True),
        ),
        migrations.AddField(
            model_name="cluster",
            name="platform_override",
            field=models.CharField(blank=True, max_length=40, null=True),
        ),
        migrations.AddField(
            model_name="cluster",
            name="seller_tier_override",
            field=models.CharField(blank=True, max_length=20, null=True),
        ),
    ]
