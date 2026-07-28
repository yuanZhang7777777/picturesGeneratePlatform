import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Placeholder loop for future async prompt jobs; prompt MVP is synchronous."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--sleep", type=float, default=10.0)

    def handle(self, *args, **options):
        if options["once"]:
            self.stdout.write("processed=0")
            return
        while True:
            time.sleep(options["sleep"])
