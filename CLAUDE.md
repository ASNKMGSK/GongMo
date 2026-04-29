# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this folder is

This is a **design + development workspace** (not a git repo). It bundles together:

- **Design artifacts** — the authoritative plan for the AI QA Agent system (STT 전사 기반 콜센터 품질평가).
- **An embedded code repo** — `V2-agentcore-a2a-workshop/` contains the actual implementation and **has its own CLAUDE.md**. For code-level guidance (commands, conventions, architecture of the running system, QA pipeline tuning notes, environment variables), **read `V2-agentcore-a2a-workshop/CLAUDE.md` first** — do not duplicate those instructions here.
- **Sample generator scripts** — synthesize QA golden-set transcripts for evaluating the pipeline.

When the user asks about code, cd into `V2-agentcore-a2a-workshop/` and operate from there.

## Layout

```
V2 QA개발/
├── CLAUDE.md                                 ← this file (workspace index + design cheatsheet)
├── AI_QA_Agent_Design_Document_v2.pdf        ← authoritative design (PDF, ~182KB)
├── _design_v2.txt                            ← extracted text of the PDF (1140 lines, grep-friendly)
├── STT_기반_통합_상담평가표_v2.xlsx         ← rubric SSoT (6 sheets)
├── _gen_samples.py                           ← complete sample generator (has main(), outputs xlsx + JSON)
├── _gen_samples_v2.py                        ← WIP partial (SAMPLES dict only, no main — imports ITEM_META etc.)
├── proto.zip                                 ← earlier prototype snapshot
└── V2-agentcore-a2a-workshop/                ← code repo (see its CLAUDE.md)
    └── packages/
        ├── agentcore-agents/qa-pipeline/     ← V2 4-Layer LangGraph pipeline (single-tenant, EC2 deployed)
        ├── qa-pipeline-multitenant/          ← Pool-model multi-tenant variant (Phase 0 complete, 42/42 tests)
        ├── qa-eval-pipeline/                 ← eval scripts
        ├── cdk-infra-python/                 ← CDK stacks
        └── chatbot-ui/                       ← React UI
```

## Rubric & design — concepts you need to know

These concepts drive the pipeline code. When in doubt about evaluation semantics, the xlsx is the Single Source of Truth; `_design_v2.txt` is the prose explanation.

### 18 items in 8 categories (prototype scope)

Rubric has 19 items / 100 points; item 3 ("말겹침") is **skipped** (manhole in STT quality) and fixed at max score, leaving **18 evaluable items**. Code maps these to 8 sub-agents (greeting / understanding / courtesy / mandatory / scope / proactiveness / work_accuracy / incorrect_check — see the sub-project CLAUDE.md for the filename↔function mismatches).

Item 15 ("정확한 안내") is **weighted 15pt** in v2/v3 (was 10pt originally) — it is the single largest item and requires the business-knowledge RAG to judge. Without that RAG it must fall back to `partial_with_review` mode.

### 6 evaluation modes (must be emitted on every item result)

| mode | meaning |
|---|---|
| `full` | complete evaluation using all available info |
| `structural_only` | content unverifiable due to PII masking — judge procedure/structure only (e.g., 고객정보 확인) |
| `compliance_based` | judged on rule/procedure compliance, not content accuracy (items #17/#18 — 개인정보) |
| `partial_with_review` | AI drafts; human review required (default for #15 when knowledge RAG absent) |
| `skipped` | situation didn't occur, score = max (e.g., 쿠션어 when no refusal happened) |
| `unevaluable` | STT quality too poor; routed entirely to human |

### 4-Layer architecture (referenced across the code)

```
Input → Layer 1 전처리 (quality / segments / PII / deduction / rule_pre_verdicts / intent_type)
      → Layer 2 Category Sub-Agents (8 parallel: Group A 인사/경청/언어/니즈 · Group B 설명력/적극성/정확도/개인정보)
      → Layer 3 Orchestrator (aggregate · overrides · consistency · grader)
      → Layer 4 Post (confidence · tier_router · evidence_refiner · report_generator_v2)
```

Layer 1 runs **before** sub-agents and emits `deduction_triggers` on a side channel that the Orchestrator applies as hard overrides — they are not mixed into sub-agent scoring.

### 3 RAGs (each has a specific job — do not blend)

1. **Golden-set RAG** — few-shot examples per item × score-band × intent × segment. Retrieval key = `item + intent + segment`.
2. **Reasoning RAG** — embeddings of past human judgment *rationales* (not transcripts). Used for Confidence and consistency audit.
3. **Business-knowledge RAG** — manuals / FAQ / product data. **Only** #15 "정확한 안내" depends on this.

Forbidden RAG uses (explicit in design §7.5): weighted-averaging past human scores into current scores; averaging "strict" vs "lenient" evaluators; pure transcript-semantic retrieval without intent filter; feeding unlabeled human score pools into RAG.

### 4 Confidence signals (weighted per-item, not OR/AND)

LLM self-confidence (1–5, forced in prompt) · Rule-vs-LLM agreement · RAG similar-case score stddev · Evidence quality. Weights are item-dependent — e.g., #15 weights Evidence and Rule-agreement heavily; #7 쿠션어 weights Self-Confidence and RAG stddev heavily.

### Override / deduction categories (Layer 1 + Layer 3, never Layer 2)

- 불친절 (욕설/비하/임의 단선) → **전체 평가 0점** + 관리자 통보
- 개인정보 유출 → 해당 항목 0점 + 별도 보고
- 오안내 후 미정정 → 업무정확도 대분류 전체 0점
- STT 품질 저하 → 평가 보류, 전건 인간 검수

### Masking policy (v1 symbolic → v2 categorical)

STT currently outputs `***` for all PII (symbolic). Design mandates a **PII normalization layer in Layer 1** as the sole site that understands the masking format, so that a future switch to categorical masks (`[NAME]`, `[PHONE]`, etc.) requires no change elsewhere. Any direct handling of `***` outside Layer 1 is a bug.

## Sample generator scripts (top-level)

Both scripts target Korean call-center QA golden samples matching the original `tb_ai_ta_data` JSON format. Output default: `C:\Users\META M\Desktop\QA샘플데이터_v3_생성\` (hardcoded `OUT_DIR`).

### `_gen_samples.py` — complete (1462 lines)

Canonical generator. Produces 20 samples (4 Tier × 5) plus a unified evaluation xlsx. Run:

```bash
python _gen_samples.py
```

Outputs:
- `QA샘플데이터_v3_생성/{tier}/(opus4.7 max)_{id}_{site}_{tone}_{brand}_{slug}.json` — one per sample, matches original 668451 format.
- `QA샘플데이터_v3_생성/(opus4.7 max)_QA샘플_평가표_통합.xlsx` — 20 per-sample sheets + summary sheet, uses `V3_CRITERIA` rubric text.

Tier bands: T1 95–100 (우수), T2 85–94, T3 65–84, T4 ≤64. Transcripts are 70–100 turns each, formatted as `상담사: "..."` / `고객: "..."`.

### `_gen_samples_v2.py` — partial WIP (478 lines, **no `main()`**)

A work-in-progress variant. Only defines `ITEM_META`, `V3_CRIT`, and a partial `SAMPLES` dict (currently ends mid-structure around line 478). **Running it directly does nothing** — it's meant to be imported or finished. If you need to extend samples, either (a) append to the complete `_gen_samples.py`, or (b) port the WIP back into the canonical script and delete the WIP.

### Sample `scores` dict shape

Each sample carries a `scores[item_no] = (points, rationale, [evidence_quotes])` tuple. The evidence list holds `상담사#<turn>: "..."` strings produced by the `q(sp_turn, text)` helper. This evidence shape is what the pipeline's Evidence check expects, so new samples must follow it exactly.

## When working in this workspace

- **For code changes**: work inside `V2-agentcore-a2a-workshop/` and follow its CLAUDE.md (ruff, pnpm, Python 3.13 via `~/.conda/envs/py313/python.exe`, conventional commits, Apache-2.0 headers, etc.).
- **For rubric/spec questions**: `_design_v2.txt` for full prose context, xlsx for the authoritative scoring matrix.
- **For sample data**: use `_gen_samples.py` as the reference; don't invent a new generator — extend the existing one so `ITEM_META` / `V3_CRITERIA` stay in one place.
- **This folder is not a git repo** — there's no `.git` at this level. The code subdirectory may or may not be a repo depending on how it was cloned; check before assuming git commands will work from the workspace root.
