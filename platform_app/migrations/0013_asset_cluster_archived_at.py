from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("platform_app", "0012_cluster_same_product_relation"),
    ]

    operations = [
        migrations.AddField(
            model_name="asset",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cluster",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
