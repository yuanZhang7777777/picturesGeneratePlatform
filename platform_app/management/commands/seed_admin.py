import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update the initial platform admin user."

    def add_arguments(self, parser):
        parser.add_argument("--username", default=os.getenv("ADMIN_USERNAME", "admin"))
        parser.add_argument("--password", default=os.getenv("ADMIN_PASSWORD"))

    def handle(self, *args, **options):
        username = options["username"]
        password = options["password"]
        if not password:
            raise CommandError("Provide --password or ADMIN_PASSWORD")

        user_model = get_user_model()
        user, _ = user_model.objects.get_or_create(username=username)
        user.role = user_model.Role.ADMIN
        user.is_staff = True
        user.is_superuser = True
        user.must_change_password = False
        user.set_password(password)
        user.save()
        self.stdout.write(f"admin user ready: {username}")
