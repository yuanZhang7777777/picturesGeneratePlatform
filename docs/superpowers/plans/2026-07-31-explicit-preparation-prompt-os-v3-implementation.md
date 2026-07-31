# Explicit Preparation and Prompt OS v3 Implementation Plan

> **For agentic workers:** Implement with scoped TDD. Agents must not edit outside their assigned file boundary, must report tests and risks, and must not deploy or make paid API calls.

**Goal:** Make organize-mode imports AI-free, add an explicit preparation step, enforce current N7-approved prompts before generation, publish full Prompt OS v3 templates, and replace the workbench with the confirmed compact card workflow.

**Architecture:** Django remains the source of truth for project settings, cluster preparation state, PromptVersion snapshots, generation gates, and administrator prompt versions. React renders a compact project toolbar and square product grid from the existing project snapshot. Preparation runs N1-N7 without creating Generation rows; formal generation consumes only current approved PromptVersions.

**Tech Stack:** Django 5.2, PostgreSQL-compatible Django models, React 19, TypeScript, TanStack Query, dnd-kit, APIMart (`gpt-5-nano-2025-08-07`, `deepseek-v4-pro`, `gpt-image-2`).

## Global Constraints

- New project defaults are `platform=generic`, `market=SEA`, `size=1:1`, and `resolution=1K`.
- Organize imports create assets and clusters but make no AI request.
- Auto imports run the same N1-N7 preparation path and continue to generation only after a current N7 pass.
- Manual product names are never overwritten; only blank names may be filled by N2.
- A non-empty product style completely replaces project style; an empty product style uses project style.
- No production fallback may compile or create a prompt during formal generation.
- System prompts are not limited to 3500 characters; only the final per-image GPT Image 2 prompt is limited to 3500 characters.
- Do not edit `docs/project/用户操作流程以及相关触发.md`.

## Task 1: Backend preparation contract and hard generation gate

- Add project settings update and explicit selected-cluster preparation endpoints.
- Add cluster platform/market overrides and preparation stage/progress fields.
- Stop organize upload and ERP import from requesting preparation.
- Persist a current cluster/config/template/rule/model fingerprint in preparation snapshots.
- Reject formal generation unless current PromptVersions were created by N4/N6 and passed N7 for the current fingerprint.
- Remove the production `compile_slot_prompt()` fallback from generation creation.
- Preserve previous PromptVersions, identity snapshots, generations, and results.
- Resolve the existing post-assert/pre-provider-submit race and enforce the strict N1/N2 schemas documented in the Prompt OS specification.

## Task 2: Prompt OS v3 templates

- Migrate the complete identity, quantity topology, usage relationship, marketing scene, localization, and anti-repetition rules from `E:/Project/出图需求/docs/plans/2026-07-19-coze-shopee-v2-workflow-implementation.md`.
- Publish shared N1-N4/N8-N9 templates plus `N5|N6|N7.generic`, `.shopee`, and `.tiktok` variants.
- Include strict output schemas and one repair attempt.
- Add a generic marketplace/SEA English 1+8 strategy rather than using a short fallback.
- Keep templates versioned in `PromptNodeTemplate` and make seeded content identical to the runtime system message.

## Task 3: Compact workbench and inline product expansion

- Render one compact project toolbar with Chinese platform/market options, project style, add-product, prepare, generate, and results actions.
- Render square product cards with image, name, platform, market, brief, collapsed product style, selection, and stage.
- Remove relationship selectors, filenames, follow-project copy, and legacy English slot labels.
- Implement whole-card merge, thumbnail move, and blank-area split with dnd-kit.
- Expand one selected product across its entire grid row, keeping the original card at left and identity/facts/1+8 prompts at right.
- While expanded, the first outside click closes and consumes the action; a later click may open another card. Drag gestures remain active.
- Display N1-N7 and generation stage progress from the project snapshot.

## Task 4: Administrator prompt center

- Add an administrator-only Chinese React page and minimal APIs for listing, viewing, creating drafts, publishing, and republishing historical PromptNodeTemplate versions.
- Show the exact runtime system prompt, user message template, output schema, model, platform scope, version, status, and history.
- Operators continue to see only identity cards, marketing plans, and final per-slot prompts.

## Task 5: Integration, documentation, and release

- Verify backend and frontend suites, build, Django checks, migration drift, upload/ERP organize smoke, explicit preparation, hard generation blocking, drag interactions, and administrator prompt visibility.
- Update the requirements boundary, current product design, Prompt OS specification, phased roadmap, STATUS, README/runbook where runtime behavior changed.
- Deploy through the existing locked preview deployment process only after local verification; do not perform paid 1+8 generation without explicit release approval.
