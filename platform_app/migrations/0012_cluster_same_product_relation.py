from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("platform_app", "0011_batch_seller_tier"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cluster",
            name="relation_type",
            field=models.CharField(
                choices=[
                    ("single_product", "Single product"),
                    ("same_product", "Same product references"),
                    ("variant_group", "Variant group"),
                ],
                default="single_product",
                max_length=40,
            ),
        ),
    ]
