import os
import time
from concurrent.futures import ThreadPoolExecutor

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from platform_app.services import generation_worker_batch_size, process_generation_once


class Command(BaseCommand):
    help = "Run the generation queue worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--sleep", type=float, default=5.0)
        parser.add_argument("--concurrency", type=int, default=int(os.getenv("GENERATION_WORKER_CONCURRENCY", "32")))

    def process_batch(self, concurrency):
        concurrency = generation_worker_batch_size(concurrency)
        if concurrency <= 1:
            return process_generation_once()

        def run_once(_):
            close_old_connections()
            try:
                return process_generation_once()
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
