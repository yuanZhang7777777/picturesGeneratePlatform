from copy import deepcopy


STANDARD_PRODUCT_HERO_NAME = "Standard white-background product hero"
STANDARD_PRODUCT_HERO_PURPOSE = (
    "Complete, accurate product on a pure white background with no promotional text or watermark"
)
STANDARD_PRODUCT_HERO_PROMPT_LINES = (
    "Standard product hero: show the complete, accurate product on a pure white background.",
    "Hero restrictions: no promotional text, text overlay, watermark, border, price, discount, badge, or lifestyle scene.",
    "Preserve any logo or mark that is visibly part of the supplied product; do not add new branding.",
)
SOURCE_PRODUCT_PHOTO_NAME = "Seller original product photo"


def is_standard_product_hero_slot(slot):
    return slot.name == STANDARD_PRODUCT_HERO_NAME or (
        slot.order == 1 and slot.name != SOURCE_PRODUCT_PHOTO_NAME
    )


def is_source_product_photo_slot(slot):
    return slot.name == SOURCE_PRODUCT_PHOTO_NAME


def standard_product_hero_slot(template):
    return (
        template.slots.filter(name=STANDARD_PRODUCT_HERO_NAME).order_by("order", "id").first()
        or template.slots.filter(order=1).first()
    )


def apply_standard_product_hero_policy(slot, prompt, input_snapshot=None):
    """Return a non-mutating, idempotent prompt/snapshot pair for the mandatory first slot."""
    snapshot = deepcopy(input_snapshot or {})
    if not is_standard_product_hero_slot(slot):
        return prompt, snapshot
    snapshot["standard_product_hero"] = True
    return prompt.strip(), snapshot
