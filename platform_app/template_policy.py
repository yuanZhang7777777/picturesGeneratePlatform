STANDARD_PRODUCT_HERO_NAME = "Standard white-background product hero"
STANDARD_PRODUCT_HERO_PURPOSE = (
    "Complete, accurate product on a pure white background with no promotional text or watermark"
)
STANDARD_PRODUCT_HERO_PROMPT_LINES = (
    "Standard product hero: show the complete, accurate product on a pure white background.",
    "Hero restrictions: no promotional text, text overlay, watermark, price, discount, badge, or lifestyle scene.",
    "Preserve any logo or mark that is visibly part of the supplied product; do not add new branding.",
)


def is_standard_product_hero_slot(slot):
    return slot.order == 1
