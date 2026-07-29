from django.db import migrations, models


def backfill_attempts(apps, schema_editor):
    sku_import_item = apps.get_model("platform_app", "SkuImportItem")
    attempts = {}
    for item in sku_import_item.objects.order_by(
        "batch_id", "sku", "created_at", "id"
    ).iterator():
        key = (item.batch_id, item.sku)
        attempts[key] = attempts.get(key, 0) + 1
        item.attempt = attempts[key]
        item.save(update_fields=["attempt"])


class Migration(migrations.Migration):

    dependencies = [
        ("platform_app", "0008_sku_import"),
    ]

    operations = [
        migrations.AddField(
            model_name="skuimportitem",
            name="attempt",
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.RunPython(backfill_attempts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="skuimportitem",
            name="attempt",
            field=models.PositiveIntegerField(),
        ),
        migrations.AddConstraint(
            model_name="skuimportitem",
            constraint=models.UniqueConstraint(
                fields=("batch", "sku", "attempt"),
                name="unique_sku_import_attempt",
            ),
        ),
    ]
