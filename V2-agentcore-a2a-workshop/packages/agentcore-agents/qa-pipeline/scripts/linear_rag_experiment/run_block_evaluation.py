# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
QA 자동평가 prototype — LLM 3단 스택 (block 단위).

가이드: Desktop/QA자동평가_구현_및_평가셋_가이드.md

흐름:
  transcript (STT JSON)
    → 0. 정규식 turn split
    → ⭐ Stage 1: Query Decomposition (인텐트 분리)  — LLM 1회
    → Stage 2: KMS Router + 분기 결정              — LLM 1회/block
    → Stage 3: Block Judge (필수 안내사항 검증)     — LLM 1회/block
    → 종합 점수 + 감점 + 증거

사용:
  python -X utf8 run_block_evaluation.py --case 668488 --limit 1
  python -X utf8 run_block_evaluation.py --limit 5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import boto3

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
RESULTS_DIR = SCRIPT_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

KMS_PATH = DATA_DIR / "kolon_kms.json"
CASES_PATH = DATA_DIR / "kolon_test_cases.json"

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

MODEL_ALIASES = {
    "sonnet": "us.anthropic.claude-sonnet-4-6",
    "opus": "us.anthropic.claude-opus-4-7",
}

BEDROCK = boto3.client("bedrock-runtime", region_name=REGION)

INTENT_OPTIONS = ["교환", "반품", "배송", "수선", "취소", "환불", "회원정보"]
INTENT_OPTIONS_WITH_OFF = INTENT_OPTIONS + ["OFF_INTENT"]
ROLE_OPTIONS = ["INTRODUCE", "DEVELOP", "RESOLVE", "JUST_COMMENT", "CHANGE"]


# ── 0. Turn 분리 (정규식, 전처리 utility) ─────────────────────────────


SPEAKER_LINE_RE = re.compile(r"^(고객|상담사)\s*:\s*(.*)$")


def split_turns(transcript: str) -> list[dict]:
    """transcript string → [{speaker, text}, ...]. 빈 turn 제거."""
    turns: list[dict] = []
    current_speaker: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        if current_speaker and current_lines:
            text = " ".join(current_lines).strip()
            if text:
                turns.append({"speaker": current_speaker, "text": text})

    for raw in transcript.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = SPEAKER_LINE_RE.match(line)
        if m:
            _flush()
            current_speaker = m.group(1)
            content = m.group(2).strip()
            current_lines = [content] if content else []
        else:
            if current_speaker:
                current_lines.append(line)
    _flush()
    return turns


# ── LLM 호출 헬퍼 ─────────────────────────────────────────────────


def _bedrock_json(prompt: str, max_tokens: int = 800) -> dict:
    """Bedrock Sonnet 4 호출 → JSON 파싱. 실패 시 _raw 만 담은 dict."""
    inference_cfg: dict = {"maxTokens": max_tokens}
    # Opus 4.7 deprecates temperature; only pass it for non-Opus models
    if "opus" not in MODEL_ID.lower():
        inference_cfg["temperature"] = 0.0
    resp = BEDROCK.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig=inference_cfg,
    )
    text = resp["output"]["message"]["content"][0]["text"]
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {"_raw": text}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return {"_raw": text}


def _bedrock_tool_use(prompt: str, tool_def: dict, max_tokens: int = 8000) -> dict:
    """Bedrock Sonnet 4.6 Tool Use 호출 → 도구 입력 JSON 반환.

    Tool Use 는 LLM 출력을 도구 스키마에 강제 매칭 → JSON 파싱 실패 X.
    실패 시 {} 반환.
    """
    inference_cfg: dict = {"maxTokens": max_tokens}
    if "opus" not in MODEL_ID.lower():
        inference_cfg["temperature"] = 0.0
    resp = BEDROCK.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig=inference_cfg,
        toolConfig={
            "tools": [{"toolSpec": tool_def}],
            "toolChoice": {"tool": {"name": tool_def["name"]}},
        },
    )
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            return block["toolUse"].get("input", {})
    return {}


# ── Stage 1: Query Decomposition (인텐트 분리) ⭐ ──────────────────


def segment_intents(turns: list[dict]) -> list[dict]:
    """turns → 인텐트별 block 리스트. LLM 1회 호출."""
    if not turns:
        return []

    indexed = "\n".join(f"[{i}] {t['speaker']}: {t['text']}" for i, t in enumerate(turns))

    prompt = f"""다음 코오롱 CS 상담 transcript 를 인텐트 단위 block 으로 분할.

인텐트 후보: {INTENT_OPTIONS}

핵심 규칙:
- 인텐트 시작 = 고객이 명확한 요청을 한 turn (실제 처리 진행 시점)
- 인텐트 종료 = 그 인텐트 응대 마무리 turn (다음 인텐트 시작 직전 또는 마무리 인사 직전)
- 단순 언급은 block 으로 만들지 말 것 — 예: 상담사가 "안 되면 반품 가야 한다" 라고만 언급하고 실제 반품 처리 안 한 경우 별개 block 아님
- 인사/본인확인/마무리 인사/추임새 turn 은 어떤 block 에도 안 들어감
- 같은 인텐트 안 sub-topic (교환→회수일정→반송장보관) 은 한 block 으로 묶음
- 인텐트 없으면 blocks=[]

예시:
✅ 정답 분리 — 고객 "교환할게요" turn 5 → 상담사 "반송장 보관" turn 28 → block: 교환, 5~28
❌ 오분리 — 상담사 안내 도중 "반품 가야 할 수도" 언급에 별개 반품 block 만든 경우 (실제 반품 처리 X)
❌ 오분리 — 마지막 "감사합니다" 마무리 인사까지 block 에 포함한 경우

Transcript:
{indexed}

JSON 만 출력:
{{"blocks": [{{"intent": "교환", "start_turn": 5, "end_turn": 28, "summary": "넥스 경량 패딩 교환 요청"}}]}}"""

    result = _bedrock_json(prompt, max_tokens=600)
    blocks = result.get("blocks", [])
    if not isinstance(blocks, list):
        return []

    valid_blocks = []
    for b in blocks:
        s = b.get("start_turn")
        e = b.get("end_turn")
        if s is None or e is None or s > e or s < 0 or e >= len(turns):
            continue
        b["block_text"] = "\n".join(
            f"[{i}] {t['speaker']}: {t['text']}"
            for i, t in enumerate(turns)
            if s <= i <= e
        )
        valid_blocks.append(b)
    return valid_blocks


# ── Stage 1 (Def-DTS 변형): Turn-level 태깅 → 규칙 기반 boundary ─

# 학습셋 전용 — 테스트셋엔 baseline (segment_intents) 만 사용
# 참고: Def-DTS (Findings of ACL 2025, arXiv 2505.21033)
#   1단: 각 turn 을 (인텐트, 역할) 로 태깅 — LLM 1회
#   2단: 태그 시퀀스 → block 경계 — 규칙 (LLM 0회)


def tag_turns_defdts(turns: list[dict]) -> list[dict]:
    """Stage 1a — 각 turn 을 (intent, role) 로 태깅. Bedrock Tool Use 강제 스키마.

    Few-shot 4 시연 + OFF_INTENT enum + 콤팩트 출력 (i, n, r 단축 키).
    """
    if not turns:
        return []

    indexed = "\n".join(f"[{i}] {t['speaker']}: {t['text']}" for i, t in enumerate(turns))
    n_turns = len(turns)

    # Tool 스키마 — enum + 도메인 description 으로 LLM attention 유도 (PARSE 연구: +60% 추출 정확도)
    tool_def = {
        "name": "emit_turn_tags",
        "description": (
            "transcript 의 각 turn 을 (intent, role) 페어로 태깅. "
            f"입력 transcript turn 수와 정확히 동일한 길이의 tags 배열을 반환할 것 (n_turns={n_turns})."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "description": f"각 turn 의 (intent, role) 태그. 정확히 {n_turns}개 항목.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "i": {
                                    "type": "string",
                                    "enum": INTENT_OPTIONS_WITH_OFF,
                                    "description": (
                                        "교환=상품 교체 요청 처리; 반품=상품 환불·회수 처리; "
                                        "배송=배송 상태/지연/완료 문의 처리; 수선=A/S 수선 의뢰 처리; "
                                        "취소=주문 취소 요청 처리; 환불=결제수단 환불 처리; "
                                        "회원정보=회원 ID/PW/정보 변경 처리; "
                                        "OFF_INTENT=인사·본인확인·마무리·추임새·외부 구매처(지마켓 등) 안내 등 KMS 처리 대상 아닌 turn"
                                    ),
                                },
                                "r": {
                                    "type": "string",
                                    "enum": ROLE_OPTIONS,
                                    "description": (
                                        "INTRODUCE=새 인텐트 첫 진입 turn (고객 첫 요청 또는 상담사 정책 안내 시작 중 빠른 것); "
                                        "DEVELOP=같은 인텐트 진행 (sub-topic·회수일정·반송장·검수 모두 포함); "
                                        "RESOLVE=인텐트 마무리 turn (접수 완료 확정 등); "
                                        "JUST_COMMENT=단순 추임새 ('네', '예', '아하'); "
                                        "CHANGE=다른 인텐트로 전환되는 turn (교환 진행 중 고객이 '반품할게요' 등)"
                                    ),
                                },
                            },
                            "required": ["i", "r"],
                        },
                    }
                },
                "required": ["tags"],
            }
        },
    }

    # Few-shot 시연 (4 종 패턴) — Stage 1 의 엣지 케이스 학습
    few_shot = """### 시연 1 — 단일 명확 (반품)
[0] 상담사: 안녕하세요 코오롱 고객센터입니다.
[1] 고객: 티셔츠 반품 좀 할게요.
[2] 상담사: 텍 제거나 외부 착용 없으셨죠.
[3] 상담사: 반품 배송비 2500원 차감 처리해 드릴게요.
→ tags: [{"i":"OFF_INTENT","r":"INTRODUCE"},{"i":"반품","r":"INTRODUCE"},{"i":"반품","r":"DEVELOP"},{"i":"반품","r":"DEVELOP"}]

### 시연 2 — 멀티 (반품 → 교환 전환)
[0] 고객: 안타티카 90 사이즈 반품 접수된 거 확인 좀.
[1] 상담사: 네 반품 접수 확인 됩니다.
[2] 고객: 그러면 그거 90 사이즈로 교환 가능할까요?
[3] 상담사: 교환은 가능하시고 합포장 시 배송비 조정 가능합니다.
→ tags: [{"i":"반품","r":"INTRODUCE"},{"i":"반품","r":"RESOLVE"},{"i":"교환","r":"CHANGE"},{"i":"교환","r":"DEVELOP"}]

### 시연 3 — 외부 구매처 (전부 OFF_INTENT)
[0] 고객: 다운 패딩 교환 가능한가 해서요.
[1] 상담사: 어디서 구매하셨나요?
[2] 고객: 지마켓에서요.
[3] 상담사: 외부몰 구매 건은 해당 구매처로 문의 부탁드립니다.
→ tags: [{"i":"OFF_INTENT","r":"INTRODUCE"},{"i":"OFF_INTENT","r":"DEVELOP"},{"i":"OFF_INTENT","r":"DEVELOP"},{"i":"OFF_INTENT","r":"DEVELOP"}]

### 시연 4 — 교환 → 반품 전환
[0] 고객: 녹색 티셔츠 95 사이즈로 교환할게요.
[1] 상담사: 교환 배송비 6000원 발생합니다.
[2] 고객: 그러면 그냥 반품으로 해주세요.
[3] 상담사: 반품 배송비 차감 처리해 드릴게요.
→ tags: [{"i":"교환","r":"INTRODUCE"},{"i":"교환","r":"DEVELOP"},{"i":"반품","r":"CHANGE"},{"i":"반품","r":"DEVELOP"}]"""

    prompt = f"""다음 코오롱 CS 상담 transcript 의 각 turn 을 (intent, role) 로 태깅.

태깅 규칙:
- 모든 {n_turns}개 turn 에 정확히 1개씩 (intent, role) 부여 — 빠짐 없이
- 인사/본인확인/마무리/추임새/외부 구매처 안내 turn = OFF_INTENT
- 모든 turn 이 OFF_INTENT 면 정상 (외부 구매 케이스 등)
- 같은 인텐트 sub-topic 은 모두 DEVELOP (회수일정, 반송장, 검수 등 별개 INTRODUCE 만들지 X)
- 인텐트 시작 (INTRODUCE) = 고객 첫 요청 turn 보다 상담사 정책/안내 시작이 더 빠르면 그 turn 부터 (block 시작점 너무 늦게 잡지 말 것)

{few_shot}

### 평가 대상 transcript ({n_turns} turns)
{indexed}

위 transcript 의 모든 {n_turns} turn 에 대해 emit_turn_tags 도구로 태깅 출력."""

    result = _bedrock_tool_use(prompt, tool_def, max_tokens=12000)
    raw_tags = result.get("tags", [])
    if not isinstance(raw_tags, list):
        return []

    # 단축 키 (i, r) → 풀 키 (intent, role) 정규화
    tags: list[dict] = []
    for t in raw_tags:
        if not isinstance(t, dict):
            continue
        intent = t.get("i") or t.get("intent")
        role = t.get("r") or t.get("role")
        if intent and role:
            tags.append({"intent": intent, "role": role})
    return tags


def boundary_from_tags(turns: list[dict], tags: list[dict]) -> list[dict]:
    """Stage 1b — 태그 시퀀스 → block 리스트 (규칙, LLM 호출 없음).

    lenient 매칭: tags 길이와 turns 길이가 안 맞아도 min(len(tags), len(turns)) 로 처리.
    꼬리 누락된 부분은 마지막 인텐트의 DEVELOP 으로 외삽 (블록 끝까지).
    """
    if not tags:
        return []

    n_turns = len(turns)
    n_tags = len(tags)
    n_proc = min(n_turns, n_tags)

    blocks: list[dict] = []
    cur_intent: str | None = None
    cur_start: int | None = None
    cur_end: int | None = None

    def _flush() -> None:
        if cur_intent is not None and cur_start is not None and cur_end is not None:
            blocks.append({
                "intent": cur_intent,
                "start_turn": cur_start,
                "end_turn": cur_end,
                "summary": f"{cur_intent} 처리 (Def-DTS)",
            })

    for i in range(n_proc):
        tag = tags[i]
        intent = tag.get("intent") if isinstance(tag, dict) else None
        if intent == "OFF_INTENT" or intent not in INTENT_OPTIONS:
            continue

        if cur_intent is None:
            cur_intent = intent
            cur_start = i
            cur_end = i
        elif intent == cur_intent:
            cur_end = i
        else:
            _flush()
            cur_intent = intent
            cur_start = i
            cur_end = i

    # 꼬리 외삽: tags 가 부족하면 마지막 block 의 end_turn 을 transcript 끝까지 늘림
    # (실제 KMS statement 가 transcript 후반에 있으면 누락 방지)
    if n_tags < n_turns and cur_intent is not None:
        cur_end = n_turns - 1

    _flush()

    for b in blocks:
        s, e = b["start_turn"], b["end_turn"]
        b["block_text"] = "\n".join(
            f"[{i}] {t['speaker']}: {t['text']}"
            for i, t in enumerate(turns)
            if s <= i <= e
        )
    return blocks


def segment_intents_defdts(turns: list[dict]) -> tuple[list[dict], list[dict]]:
    """Def-DTS 식 2단: turn-level 태깅 → 규칙 boundary. (blocks, tags) 반환."""
    if not turns:
        return [], []
    tags = tag_turns_defdts(turns)
    if not tags or len(tags) != len(turns):
        return [], tags
    blocks = boundary_from_tags(turns, tags)
    return blocks, tags


# ── Stage 1 (Set 방식): boundary 없음, 인텐트 set 만 검출 ─────────


def detect_intent_set(turns: list[dict]) -> dict:
    """Stage 1' (Set) — 전체 transcript → 처리 진행된 인텐트 set.

    boundary 검출 없이 어떤 인텐트가 실제 처리됐는지 set 으로만 반환.
    Tool Use 로 enum 강제, 도메인 description 으로 OOS / 처리됨 구분.
    """
    if not turns:
        return {"intents": [], "rationale": "empty_transcript"}

    indexed = "\n".join(f"[{i}] {t['speaker']}: {t['text']}" for i, t in enumerate(turns))

    tool_def = {
        "name": "emit_intent_set",
        "description": (
            "전체 transcript 에서 실제 처리가 진행된 인텐트 set 을 반환. "
            "단순 언급은 제외 (실제 접수/안내가 진행된 것만)."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "intents": {
                        "type": "array",
                        "description": (
                            "실제 처리가 진행된 인텐트 목록. 중복 없이. "
                            "외부 구매처 안내만 하고 종료된 경우 빈 배열 []."
                        ),
                        "items": {
                            "type": "string",
                            "enum": INTENT_OPTIONS,
                        },
                    },
                    "rationale": {
                        "type": "string",
                        "description": "각 인텐트가 실제 처리되었다고 판단한 짧은 근거 (turn 인덱스 인용 권장)",
                    },
                },
                "required": ["intents", "rationale"],
            }
        },
    }

    prompt = f"""다음 코오롱 CS 상담 transcript 에서 **실제 처리가 진행된 인텐트** 만 set 으로 검출.

인텐트 후보 (7 종):
- 교환 / 반품 / 배송 / 수선 / 취소 / 환불 / 회원정보

판단 기준:
- ✅ 검출 — 고객이 요청 + 상담사가 안내 / 접수 진행 (예: "교환 접수 완료", "반품 배송비 안내")
- ❌ 비검출 — 단순 언급만 있고 실제 처리 없음 (예: "교환은 이번엔 어렵습니다" 만 듣고 종료)
- ❌ 비검출 — 외부 구매처 (지마켓/11번가 등) 만 안내하고 종료 → intents=[]
- ✅ 멀티 — 한 transcript 안 두 인텐트 모두 처리되면 둘 다 (예: 반품 접수 후 교환 추가 → ["반품", "교환"])

⚠ 환불 인텐트 특별 규칙 — over-detection 방지:
- ❌ 비검출 — 반품/취소의 마지막 단계로 "결제수단 환불 영업일 3-5일" 같은 환불 처리 안내가 **자동으로 포함된 경우** (이건 반품/취소 인텐트의 일부)
- ✅ 검출 — **환불 단독 처리** 일 때만 (예: 결제수단 변경 환불, 가상계좌→카드 변경, 기존 처리 건의 환불 일정 문의)
- 일반 패턴: 반품 인텐트 = 반품+환불 안내까지 모두 포함. 환불 별도 검출 X
- 일반 패턴: 취소 인텐트 = 취소+환불 안내까지 모두 포함. 환불 별도 검출 X

예시:
- "티셔츠 반품 접수 완료, 환불 영업일 3-5일" → intents=["반품"] (환불은 반품 일부, 별도 X)
- "주문 취소 완료, 결제 수단으로 환불" → intents=["취소"] (환불은 취소 일부, 별도 X)
- "이전에 반품한 건 환불 언제 되나요?" → intents=["환불"] (기존 건 환불 단독 문의)
- "교환 가능하지만 외부 구매라 처리 불가" → intents=[]
- "반품 접수 → 그러면 교환으로 변경" → intents=["반품", "교환"]
- "배송지 변경 처리 완료" → intents=["배송"]

### 평가 대상 transcript ({len(turns)} turns)
{indexed}

emit_intent_set 도구로 검출 결과 출력."""

    result = _bedrock_tool_use(prompt, tool_def, max_tokens=2000)
    intents = result.get("intents") or []
    if not isinstance(intents, list):
        intents = []
    intents = [i for i in intents if i in INTENT_OPTIONS]
    return {
        "intents": intents,
        "rationale": result.get("rationale", ""),
    }


def segment_intents_set(turns: list[dict]) -> tuple[list[dict], dict]:
    """Set 방식: boundary 없는 가상 block 으로 변환. (blocks, detection_meta) 반환.

    각 검출 인텐트마다 transcript 전체 (turn 0 ~ 마지막) 를 block_text 로 사용.
    Stage 2 (KMS routing), Stage 3 (statement judge) 가 그대로 동작.
    """
    if not turns:
        return [], {"intents": [], "rationale": "empty"}

    detection = detect_intent_set(turns)
    intents = detection.get("intents", [])

    if not intents:
        return [], detection

    full_block_text = "\n".join(
        f"[{i}] {t['speaker']}: {t['text']}" for i, t in enumerate(turns)
    )
    blocks = [
        {
            "intent": intent,
            "start_turn": 0,
            "end_turn": len(turns) - 1,
            "block_text": full_block_text,
            "summary": f"{intent} 처리 (Set 방식, 전체 transcript)",
        }
        for intent in intents
    ]
    return blocks, detection


# ── Stage 2: KMS Router + 분기 결정 (통합) ────────────────────────


def route_block_to_kms(block: dict, kms_rows: list[dict]) -> dict:
    """block + KMS 표 → 매칭 KMS 행 (pid + branch + decision_type). LLM 1회."""
    table_lines = ["| pid | intent | branch | condition | keywords |", "|---|---|---|---|---|"]
    for r in kms_rows:
        cond = (r.get("condition") or "")[:100].replace("|", " ").replace("\n", " ")
        kws = ", ".join((r.get("required_keywords") or [])[:6])
        table_lines.append(f"| {r['pid']} | {r['intent']} | {r['branch']} | {cond} | {kws} |")
    table_md = "\n".join(table_lines)

    prompt = f"""다음 KMS 표에서 block 에 가장 부합하는 행을 1개 선택 + 분기 결정.

KMS 표:
{table_md}

Block (인텐트 후보={block.get('intent')}, summary={block.get('summary', '')}):
{block.get('block_text')}

규칙:
- pid 는 표의 pid 컬럼 그대로
- intent + branch 둘 다 정확히 식별 (예: 교환 → 무료/유료)
- 분기 결정 종류:
  - Type 1 (slot filling): 고객이 명시한 선택 (예: "택배로 할게요" → 택배 분기)
  - Type 2 (post-hoc): 상담사가 X 안내를 했나 (예: 배송비 안내 → 유료, 안 함 → 무료)
- 매칭 없으면 pid="none"

JSON 만 출력:
{{"pid": "교환_무료", "branch": "무료", "decision_type": "type_2", "evidence_turn": 17, "reason": "..."}}"""

    return _bedrock_json(prompt, max_tokens=300)


# ── Stage 3: Block Judge (필수 안내사항 검증) ─────────────────────


def judge_block(block: dict, kms_row: dict) -> dict:
    """block 안 상담사 발화 vs KMS required_statements. LLM 1회."""
    statements = kms_row.get("required_statements") or []
    if not statements:
        return {"satisfied": [], "missing": [], "note": "no_required_statements"}

    agent_lines = []
    for line in (block.get("block_text") or "").split("\n"):
        m = re.match(r"^\[\d+\]\s+상담사\s*:\s*(.*)$", line)
        if m:
            agent_lines.append(m.group(1).strip())
    agent_text = "\n".join(agent_lines) if agent_lines else block.get("block_text", "")

    stmt_md = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(statements))

    prompt = f"""상담사 발화가 KMS 의 필수 안내사항을 의미상 전달했나?

분기: {kms_row.get('intent')} - {kms_row.get('branch')}
필수 안내사항:
{stmt_md}

상담사 발화 (block 안, 고객 발화 제외):
{agent_text}

평가 규칙:
- 표현이 달라도 의미상 동일하게 안내했으면 satisfied
  예: KMS "회수 기사 2~3일 이내" vs 상담사 "기사가 한 이틀 정도 후" → satisfied
- 누락 또는 부정확 안내면 missing
  예: KMS "회수 기사 2~3일 이내" vs 상담사 "기사가 갈 거예요" → missing (시간 누락)
- evidence 는 답변 원문에서 짧게 발췌

JSON 만 출력:
{{"satisfied": [1,3], "missing": [2,4], "evidence": {{"1": "발췌...", "3": "..."}}}}"""

    return _bedrock_json(prompt, max_tokens=800)


# ── 종합 평가 ─────────────────────────────────────────────────────


def evaluate_case(transcript: str, kms_rows: list[dict], method: str = "baseline") -> dict:
    t0 = time.time()
    turns = split_turns(transcript)
    if not turns:
        return {"status": "no_turns", "n_turns": 0, "blocks": []}

    # Stage 1
    tags: list[dict] = []
    detection_meta: dict | None = None
    if method == "defdts":
        blocks, tags = segment_intents_defdts(turns)
    elif method == "set":
        blocks, detection_meta = segment_intents_set(turns)
    else:
        blocks = segment_intents(turns)

    # Stage 2 + 3
    kms_by_pid = {r["pid"]: r for r in kms_rows}
    block_results = []
    for block in blocks:
        routed = route_block_to_kms(block, kms_rows)
        pid = (routed or {}).get("pid")
        kms_row = kms_by_pid.get(pid) if pid and pid != "none" else None

        judgment = None
        if kms_row and not kms_row.get("is_evaluation_skip"):
            judgment = judge_block(block, kms_row)

        block_results.append({
            "block": {
                "intent": block.get("intent"),
                "start_turn": block.get("start_turn"),
                "end_turn": block.get("end_turn"),
                "summary": block.get("summary"),
            },
            "router": routed,
            "kms_pid": pid,
            "kms_intent": kms_row.get("intent") if kms_row else None,
            "kms_branch": kms_row.get("branch") if kms_row else None,
            "judgment": judgment,
        })

    # 점수 종합
    total_satisfied = 0
    total_required = 0
    deductions: list[dict] = []
    for r in block_results:
        j = r.get("judgment") or {}
        sat = j.get("satisfied") or []
        miss = j.get("missing") or []
        if not isinstance(sat, list) or not isinstance(miss, list):
            continue
        total_satisfied += len(sat)
        total_required += len(sat) + len(miss)
        for idx in miss:
            deductions.append({
                "block_intent": r["kms_intent"],
                "block_branch": r["kms_branch"],
                "kms_pid": r["kms_pid"],
                "statement_idx": idx,
            })

    return {
        "status": "ok",
        "method": method,
        "n_turns": len(turns),
        "n_blocks": len(blocks),
        "n_kms_matched": sum(1 for r in block_results if r["kms_pid"] and r["kms_pid"] != "none"),
        "n_judged": sum(1 for r in block_results if r["judgment"]),
        "statement_pass_rate": (total_satisfied / total_required) if total_required else None,
        "total_satisfied": total_satisfied,
        "total_required": total_required,
        "deductions": deductions,
        "block_results": block_results,
        "turn_tags": tags if method == "defdts" else None,
        "intent_set_detection": detection_meta if method == "set" else None,
        "elapsed_s": round(time.time() - t0, 2),
    }


# ── main ──────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=None, help="case_id 1개 (지정 시 해당 케이스만)")
    ap.add_argument("--limit", type=int, default=1, help="처리할 케이스 수")
    ap.add_argument(
        "--split",
        default=None,
        choices=["학습셋", "테스트셋", "all"],
        help="학습셋 / 테스트셋 / all (default: case_id / limit 만 적용, 멀티테넌트 자동 제외)",
    )
    ap.add_argument(
        "--method",
        default="baseline",
        choices=["baseline", "defdts", "set"],
        help=(
            "Stage 1 방식: "
            "baseline (단발 boundary 호출) / "
            "defdts (turn-level 태깅 + 규칙 boundary) / "
            "set (boundary 없이 인텐트 set 만 검출, 전체 transcript 으로 평가)"
        ),
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=5,
        help="병렬 처리 케이스 수 (기본 5, Bedrock TPM 제한 고려)",
    )
    args = ap.parse_args()

    kms = json.loads(KMS_PATH.read_text(encoding="utf-8"))
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))

    # 멀티테넌트 합성 케이스는 항상 제외 (case_id 가 668451-A 처럼 - 포함)
    cases = [c for c in cases if "-" not in str(c["case_id"])]

    if args.case:
        cases = [c for c in cases if str(c["case_id"]) == args.case]
    elif args.split and args.split != "all":
        cases = [c for c in cases if c.get("split") == args.split]
    cases = cases[: args.limit]

    print(f"KMS rows: {len(kms)}")
    print(f"Cases: {len(cases)}")
    print(f"Model: {MODEL_ID}")
    print(f"Method: {args.method}")
    print(f"Split: {args.split or '(auto)'}")
    print(f"Workers: {args.workers}")
    print()

    print_lock = threading.Lock()
    t_total_start = time.time()

    def _run_one(idx: int, c: dict) -> dict:
        result = evaluate_case(c.get("transcript", ""), kms, method=args.method)
        rec = {
            "case_id": c["case_id"],
            "filename": c.get("filename"),
            "split": c.get("split"),
            "gt_intents": c.get("gt_intents"),
            **result,
        }
        with print_lock:
            print(
                f"[{idx}/{len(cases)}] case={c['case_id']} | "
                f"turns={result.get('n_turns')} blocks={result.get('n_blocks')} "
                f"matched={result.get('n_kms_matched')} "
                f"pass_rate={result.get('statement_pass_rate')} "
                f"elapsed={result.get('elapsed_s')}s"
            )
            for j, br in enumerate(result.get("block_results", [])):
                block = br.get("block", {})
                judg = br.get("judgment") or {}
                print(
                    f"    block[{j}] intent={block.get('intent')} "
                    f"turns={block.get('start_turn')}~{block.get('end_turn')} "
                    f"→ kms={br.get('kms_pid')} "
                    f"satisfied={judg.get('satisfied')} missing={judg.get('missing')}"
                )
        return rec

    out: list[dict] = [None] * len(cases)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run_one, i, c): i for i, c in enumerate(cases, 1)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                out[idx - 1] = fut.result()
            except Exception as e:
                with print_lock:
                    print(f"[{idx}] ERROR: {e}")
                out[idx - 1] = {"case_id": cases[idx - 1]["case_id"], "error": str(e)}

    out = [r for r in out if r is not None]
    print(f"\nTotal wall clock: {round(time.time() - t_total_start, 1)}s")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = f"_{args.method}" if args.method != "baseline" else ""
    if args.split and args.split != "all":
        suffix += f"_{args.split}"
    out_path = RESULTS_DIR / f"block_eval_{ts}{suffix}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
