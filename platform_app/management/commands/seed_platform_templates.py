from django.core.management.base import BaseCommand

from platform_app.models import OutputSlot, OutputTemplate, RuleProfile


GLOBAL_NAME = "Global marketplace baseline"
VERSION = "2026.07"
GLOBAL_TEMPLATE_KEY = "global-marketplace-baseline-template"
GLOBAL_RULE_KEY = "global-marketplace-baseline-rule"
GLOBAL_SLOTS = (
    (1, "Main listing", "Primary marketplace listing image"),
    (2, "Benefit scene", "Product benefit in context"),
    (3, "Detail/quality", "Material, detail, or quality evidence"),
    (4, "Usage scene", "Product use in a realistic scene"),
)
REGIONAL_SITES = {
    "shopee": ("SG", "MY", "TH", "VN", "PH", "ID", "TW", "BR"),
    "tiktok": ("SG", "MY", "TH", "VN", "PH"),
}


class Command(BaseCommand):
    help = "Seed the global marketplace baseline and regional draft placeholders."

    def handle(self, *args, **options):
        template, _ = OutputTemplate.objects.get_or_create(
            seed_key=GLOBAL_TEMPLATE_KEY,
            defaults={
                "platform": "global",
                "site": "",
                "name": GLOBAL_NAME,
                "version": VERSION,
                "status": OutputTemplate.Status.PUBLISHED,
                "default_size": "1:1",
                "default_resolution": "1k",
            },
        )
        for order, name, purpose in GLOBAL_SLOTS:
            OutputSlot.objects.get_or_create(
                template=template,
                order=order,
                defaults={"name": name, "purpose": purpose},
            )

        RuleProfile.objects.get_or_create(
            seed_key=GLOBAL_RULE_KEY,
            defaults={
                "platform": "global",
                "site": "",
                "name": GLOBAL_NAME,
                "version": VERSION,
                "status": RuleProfile.Status.PUBLISHED,
                "rules": {
                    "review_required": True,
                    "localized_copy": True,
                    "no_unverified_claims": True,
                },
            },
        )

        for platform, sites in REGIONAL_SITES.items():
            for site in sites:
                name = f"{platform.title()} {site} official rules pending"
                OutputTemplate.objects.get_or_create(
                    seed_key=f"{platform}-{site.lower()}-template",
                    defaults={
                        "platform": platform,
                        "site": site,
                        "name": name,
                        "version": VERSION,
                        "status": OutputTemplate.Status.DRAFT,
                        "default_size": "1:1",
                        "default_resolution": "1k",
                    },
                )
                RuleProfile.objects.get_or_create(
                    seed_key=f"{platform}-{site.lower()}-rule",
                    defaults={
                        "platform": platform,
                        "site": site,
                        "name": name,
                        "version": VERSION,
                        "status": RuleProfile.Status.DRAFT,
                    },
                )

        self.stdout.write(self.style.SUCCESS("platform templates ready"))
