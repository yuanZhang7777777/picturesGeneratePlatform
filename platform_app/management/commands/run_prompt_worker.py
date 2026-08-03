import os
import time
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from platform_app.services import process_prompt_once


class Command(BaseCommand):
    help = "Run the product preparation and prompt queue worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--sleep", type=float, default=10.0)
        parser.add_argument("--concurrency", type=int, default=int(os.getenv("PROMPT_WORKER_CONCURRENCY", "64")))

    def process_batch(self, concurrency):
        if concurrency <= 1:
            return process_prompt_once()

        def run_once(_):
            close_old_connections()
            try:
                return process_prompt_once()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            return sum(executor.map(run_once, range(concurrency)))

    def handle(self, *args, **options):
        while True:
            processed = self.process_batch(max(1, options["concurrency"]))
            if options["once"]:
                self.stdout.write(f"processed={processed}")
                return
            if processed == 0:
                time.sleep(options["sleep"])
