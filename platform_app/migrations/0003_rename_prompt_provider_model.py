from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("platform_app", "0002_prompt_os"),
    ]

    operations = [
        migrations.RenameField(
            model_name="promptversion",
            old_name="model",
            new_name="provider_model",
        ),
    ]
