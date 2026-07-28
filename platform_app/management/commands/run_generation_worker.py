import time

from django.core.management.base import BaseCommand

from platform_app.services import process_generation_once


class Command(BaseCommand):
    help = "Run the generation queue worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--sleep", type=float, default=5.0)

    def handle(self, *args, **options):
        while True:
            processed = process_generation_once()
            if options["once"]:
                self.stdout.write(f"processed={processed}")
                return
            if processed == 0:
                time.sleep(options["sleep"])
