from django.core.management.base import BaseCommand

from platform_app.models import OutputSlot, OutputTemplate, RuleProfile
from platform_app.template_policy import STANDARD_PRODUCT_HERO_NAME, STANDARD_PRODUCT_HERO_PURPOSE


GLOBAL_NAME = "Global marketplace baseline"
VERSION = "2026.07.8"
RULE_VERSION = "2026.07"
GLOBAL_TEMPLATE_KEY = "global-marketplace-baseline-template"
EIGHT_SLOT_TEMPLATE_KEY = "global-marketplace-eight-slot-template"
GLOBAL_RULE_KEY = "global-marketplace-baseline-rule"
GLOBAL_SLOTS = (
    (1, STANDARD_PRODUCT_HERO_NAME, STANDARD_PRODUCT_HERO_PURPOSE),
    (2, "Key benefit", "Show one verified product selling point"),
    (3, "Product detail", "Show material, construction, or detail evidence"),
    (4, "Function", "Show a verified product function"),
    (5, "Size and scale", "Show verified dimensions or real-world scale without unverified claims"),
    (6, "Usage", "Show realistic product use"),
    (7, "Lifestyle scene", "Show the product in a relevant lifestyle scene"),
    (8, "Packaging and accessories", "Show included packaging or verified accessories"),
)
REGIONAL_SITES = {
    "shopee": ("SG", "MY", "TH", "VN", "PH", "ID", "TW", "BR"),
    "tiktok": ("SG", "MY", "TH", "VN", "PH"),
}


class Command(BaseCommand):
    help = "Seed the global marketplace baseline and regional draft placeholders."

    def handle(self, *args, **options):
        template, created = OutputTemplate.objects.get_or_create(
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
        if not created and (template.version != VERSION or template.slots.count() != len(GLOBAL_SLOTS)):
            template, _ = OutputTemplate.objects.get_or_create(
                seed_key=EIGHT_SLOT_TEMPLATE_KEY,
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
                "version": RULE_VERSION,
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
                template, _ = OutputTemplate.objects.get_or_create(
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
                OutputSlot.objects.get_or_create(
                    template=template,
                    order=1,
                    defaults={
                        "name": STANDARD_PRODUCT_HERO_NAME,
                        "purpose": STANDARD_PRODUCT_HERO_PURPOSE,
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
