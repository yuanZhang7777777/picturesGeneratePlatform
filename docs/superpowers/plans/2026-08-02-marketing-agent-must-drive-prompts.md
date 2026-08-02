# Marketing Agent Must Drive Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 商品只要有商品名或可见商品身份，就必须经过营销 Agent 生成 1+8 策略和文案，不再退回包装图、居家模板或空文案。

**Architecture:** 最小改共享路径：N3 输入不再把 UI 补充/污染文本直接当 confirmed；N5 fallback 只做动态购买心理任务，不写死包装/国家场景；N6 fallback 生成可见营销文案并给 `gpt-image-2` 一个成品广告图指令。保留现有模型、表、队列和 API。

**Tech Stack:** Django 5.2, existing Prompt OS services, pytest. No new dependencies, no new tables.

## Global Constraints

- 整理导入零 AI；预备/正式生成才运行 N1–N7。
- 同一卡多图共同产出一套 1+8；明显非目标图不得污染商品事实。
- 只要商品名或可见商品身份存在，不得因缺规格/包装/详细事实阻断营销策划。
- 包装/包含物只有证据存在时才可安排；否则改为使用、情绪、礼物、陪伴、细节或尺度类图。
- 最终图片 Prompt 是英文控制指令；消费者可见文案由 N6 锁定目标语言。

---

### Task 1: Freeze regression tests

**Files:**
- Modify: `tests/test_prompt_os.py`

**Interfaces:**
- Consumes: `_fallback_n5_plans`, `_fallback_n6_prompt`, preparation fact-input behavior.
- Produces: failing tests for no packaging without evidence, no empty marketing copy, and polluted non-target facts not treated as confirmed.

- [ ] Add tests proving fallback N5 does not create packaging tasks without packaging facts.
- [ ] Add tests proving fallback N6 creates visible copy for marketing slots when text is allowed.
- [ ] Add tests proving product facts for N3 come from target observations/identity, not unrelated scene text.
- [ ] Run targeted pytest and confirm failures.

### Task 2: Fix shared prompt path

**Files:**
- Modify: `platform_app/services.py`
- Modify: `platform_app/prompt_templates_v3.py`

**Interfaces:**
- Produces: cleaned fact input helper, dynamic N5 fallback, marketing-copy N6 fallback.

- [ ] Add a small helper that builds confirmed N3 points from product name + N2 identity + target observations only.
- [ ] Remove packaging from fallback slot recipes unless identity/profile contains confirmed packaging or included items.
- [ ] Make fallback N5 express dynamic FAB / scene ownership / emotion / personification / identity signal tasks instead of fixed scene text.
- [ ] Make fallback N6 emit 1–3 localized visible text lines for marketing slots unless rules disable text.
- [ ] Fix repeated `the uploaded product` prompt text.
- [ ] Run targeted pytest and Django check.

### Task 3: Docs/status and deploy

**Files:**
- Modify: `docs/project/STATUS.md`
- Modify: `docs/project/PROMPT-OS-RUNTIME-NODES.md`

**Interfaces:**
- Produces: documented production behavior and deployment handoff.

- [ ] Update docs with “marketing agent required when identity/name exists”.
- [ ] Run `git diff --check`.
- [ ] Commit and push.
- [ ] Deploy through `ssh hermes-remote` with `global.lock`.
- [ ] Run remote health and Django check.
