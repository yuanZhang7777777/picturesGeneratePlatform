from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("platform_app", "0010_dual_speed_preparation"),
    ]

    operations = [
        migrations.AddField(
            model_name="batch",
            name="seller_tier",
            field=models.CharField(
                choices=[("general", "General"), ("mall", "Mall")],
                default="general",
                max_length=20,
            ),
        ),
    ]
