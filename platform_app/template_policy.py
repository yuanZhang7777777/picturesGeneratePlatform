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


def is_standard_product_hero_slot(slot):
    return slot.order == 1


def apply_standard_product_hero_policy(slot, prompt, input_snapshot=None):
    """Return a non-mutating, idempotent prompt/snapshot pair for the mandatory first slot."""
    snapshot = deepcopy(input_snapshot or {})
    if not is_standard_product_hero_slot(slot):
        return prompt, snapshot
    prompt = prompt.strip()
    missing_lines = [line for line in STANDARD_PRODUCT_HERO_PROMPT_LINES if line not in prompt]
    if missing_lines:
        prompt = "\n".join(part for part in (prompt, *missing_lines) if part)
    snapshot["standard_product_hero"] = True
    return prompt, snapshot
