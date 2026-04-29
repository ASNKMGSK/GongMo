# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
[상태: 보류 — PL 최종 확정(2026-04-20)으로 생성 취소]

Golden-set synthetic 162개 생성은 취소되어 Golden-set RAG 는 기능 구현만 진행.
이 스크립트는 향후 시니어 라벨링 워크샵 이후 참조 템플릿으로 보존. 실행 가드 있음
(환경변수 `V2_SYNTHETIC_GOLDENSET_APPROVED=1` 필요).

---

Synthetic Golden-set 생성기 — 18 항목 × 3 bucket × 3 예시 = 162 (3-단계 기준) / 216 (4-단계 #10/#15 포함).

PL 지시사항 (Q3 결정, 이후 취소됨):
  - Claude Sonnet 4 (Bedrock) 사용
  - 다양한 도메인 (금융/통신/이커머스/CS 일반) 혼합
  - 극단적/편향된 문구 회피
  - 각 예시에 `transcript_snippet, intent, segment, judgment, evidence` 5 필드 포함
  - versions: seed_v0.1_synthetic_162

V1 `nodes/llm.py` 의 Bedrock 패턴 재활용 (ChatBedrockConverse). 단, 본 스크립트는 qa-pipeline v1 의존성
(config.py / state.py 등) 없이 독립 실행되도록 경량 호출부만 사용.

실행:
    cd packages/agentcore-agents/qa-pipeline
    python v2/scripts/generate_synthetic_goldenset.py \\
        --tenant generic \\
        --out v2/tenants/generic/golden_set \\
        --model us.anthropic.claude-sonnet-4-20250514-v1:0 \\
        --per-bucket 3
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import sys
from pathlib import Path
from typing import Any


# v2/scripts → qa-pipeline 으로 2단계 상향
_PIPELINE_DIR = Path(__file__).resolve().parent.parent.parent
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

from v2.scripts._rubric_meta import DOMAIN_POOL, ITEMS, SUPPORTED_INTENTS, get_buckets_for  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("generate_synthetic_goldenset")


# ---------------------------------------------------------------------------
# Bedrock 호출 (최소 의존: boto3 + LangChain ChatBedrockConverse)
# ---------------------------------------------------------------------------


def _get_llm(model_id: str, temperature: float, max_tokens: int):
    """Bedrock Converse 모델 인스턴스 — 의존성 최소화."""
    from botocore.config import Config as BotoConfig
    from langchain_aws import ChatBedrockConverse

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    cfg = BotoConfig(
        read_timeout=300,
        connect_timeout=10,
        retries={"max_attempts": 4, "mode": "adaptive"},
        max_pool_connections=10,
    )
    return ChatBedrockConverse(
        model=model_id,
        region_name=region,
        temperature=temperature,
        max_tokens=max_tokens,
        config=cfg,
    )


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------


SYSTEM_INSTRUCTION = """너는 한국 콜센터 QA 평가 전문가다. 평가 루브릭을 보고 특정 점수를 받기에 전형적인 콜센터 상담 발화 스니펫을 설계한다.

원칙:
1. 반드시 JSON 배열만 출력 (설명 문장, 마크다운 코드 펜스 금지 — 순수 JSON).
2. 각 예시는 독립적이며 서로 다른 도메인(금융/통신/이커머스/CS 일반)을 무작위로 섞어 다양성 확보.
3. 극단적 / 편향된 / 공격적 문구는 회피. 학습된 QA 평가가 실제 상담 품질을 합리적으로 측정하도록 현실적 수준으로 작성.
4. transcript_snippet 은 `상담사: ...` / `고객: ...` 2~6 발화로 구성. 한 발화는 140자 이내.
5. 고객 개인정보(실명, 실제 주민번호, 실제 전화번호) 금지 — 가상의 이름("홍길동") 및 마스킹 형태만 사용.
6. 각 예시의 judgment 는 항상 "<점수>점 — <핵심 사유 한 문장>" 형태.
7. evidence 는 transcript_snippet 내부에서 판정을 뒷받침하는 정확한 인용 구절(한 조각).
"""


def _user_prompt(item: dict, bucket_label: str, score: int, criteria: str, per_bucket: int) -> str:
    intents = ", ".join(SUPPORTED_INTENTS)
    domain_hint = ", ".join(DOMAIN_POOL)
    notes_line = f"\n- V1 튜닝 특례: {item['v1_notes']}" if item["v1_notes"] else ""
    return f"""아래 평가 루브릭의 특정 점수 구간에 해당하는 상담 스니펫 {per_bucket}개를 생성해라.

[평가 항목]
- item_number: {item['item_number']}
- name: {item['name']}
- category: {item['category']}
- max_score: {item['max_score']}
- allowed_steps: {item['allowed_steps']}
- segment_strategy: {item['segment_strategy']}{notes_line}

[목표 점수]
- score: {score}
- bucket: {bucket_label}
- 판정 기준: {criteria}

[출력 스키마 — JSON 배열, {per_bucket}개 원소]
각 원소는 아래 5 필드만 포함:
{{
  "transcript_snippet": "상담사: ...\\n고객: ...\\n상담사: ...",
  "intent": "<{intents} 중 하나>",
  "segment": "<segment_strategy 에 맞는 발화 묶음 한 줄 요약>",
  "judgment": "{score}점 — <핵심 사유 한 문장>",
  "evidence": "<transcript_snippet 안에서 판정을 뒷받침하는 정확한 인용>"
}}

[제약]
- 도메인은 {domain_hint} 중에서 {per_bucket}개 서로 다른 도메인을 최대한 섞는다.
- intent 도 최대한 서로 다른 값으로 분산한다.
- 극단적/비현실적 문구(욕설 반복, 고객 협박, 내부 시스템 토큰 등) 금지.
- 마크다운/설명/개행 이외의 텍스트 없이, 순수 JSON 배열만 출력.

지금 JSON 배열만 출력해라:"""


_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _extract_json_array(text: str) -> list[dict]:
    # 코드 펜스 제거
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    # 여전히 배열 바깥에 서술이 붙을 수 있어 가장 큰 JSON 배열 추출
    m = _JSON_ARRAY_RE.search(t)
    if not m:
        raise ValueError(f"no JSON array found in LLM output: {text[:200]!r}")
    return json.loads(m.group(0))


# ---------------------------------------------------------------------------
# 생성
# ---------------------------------------------------------------------------


def generate_bucket(llm, item: dict, bucket_label: str, score: int, criteria: str, per_bucket: int) -> list[dict]:
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [
        SystemMessage(content=SYSTEM_INSTRUCTION),
        HumanMessage(content=_user_prompt(item, bucket_label, score, criteria, per_bucket)),
    ]
    resp = llm.invoke(messages)
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    raw = _extract_json_array(text)
    # 필드 정제 및 내구성 강화
    cleaned: list[dict] = []
    for idx, ex in enumerate(raw):
        if not isinstance(ex, dict):
            continue
        snippet = str(ex.get("transcript_snippet", "")).strip()
        if not snippet:
            continue
        cleaned.append(
            {
                "transcript_snippet": snippet,
                "intent": str(ex.get("intent", "general_inquiry")).strip() or "general_inquiry",
                "segment": str(ex.get("segment", "")).strip(),
                "judgment": str(ex.get("judgment", f"{score}점 — 자동 생성")).strip(),
                "evidence": str(ex.get("evidence", "")).strip(),
            }
        )
    if len(cleaned) < per_bucket:
        logger.warning(
            "item=%d bucket=%s got %d examples (< %d requested)",
            item["item_number"],
            bucket_label,
            len(cleaned),
            per_bucket,
        )
    return cleaned[:per_bucket]


# ---------------------------------------------------------------------------
# 파일 합성 (score-bucket 별 그룹 JSON 구조)
# ---------------------------------------------------------------------------


def _filename_for(item: dict) -> str:
    return f"{item['item_number']:02d}_{item['slug']}.json"


def build_file_payload(item: dict, examples_by_score: dict[int, list[dict]]) -> dict[str, Any]:
    """score_{n}: [...] 형태 그룹 JSON + v2.rag 호환 examples[] 유지."""
    # 기존 RAG 로더 호환: examples 필드에 flat list 저장
    flat_examples: list[dict] = []
    bucket_groups: dict[str, list[dict]] = {}

    for bucket_label, score, _criteria in get_buckets_for(item):
        group_key = f"score_{score}"
        per_bucket = examples_by_score.get(score, [])
        group = []
        for i, ex in enumerate(per_bucket):
            example_id = f"GS-{item['item_number']:02d}-{bucket_label.upper()}-{i+1:02d}"
            flat_example = {
                "example_id": example_id,
                "score": score,
                "score_bucket": _rag_bucket(bucket_label),
                "intent": ex.get("intent", "general_inquiry"),
                "segment_text": ex.get("transcript_snippet", ""),
                "rationale": ex.get("judgment", ""),
                "rationale_tags": [bucket_label],
                "evidence_refs": [ex.get("evidence", "")] if ex.get("evidence") else [],
                "rater_meta": {
                    "rater_type": "synthetic_consensus",
                    "source": "claude_sonnet_4",
                    "version": "seed_v0.1_synthetic_162",
                },
                "generation_meta": {
                    "transcript_snippet": ex.get("transcript_snippet", ""),
                    "intent": ex.get("intent", ""),
                    "segment": ex.get("segment", ""),
                    "judgment": ex.get("judgment", ""),
                    "evidence": ex.get("evidence", ""),
                },
            }
            flat_examples.append(flat_example)
            group.append(flat_example)
        bucket_groups[group_key] = group

    return {
        "item_number": item["item_number"],
        "item_name": item["name"],
        "category": item["category"],
        "max_score": item["max_score"],
        "allowed_steps": item["allowed_steps"],
        "intents": ["*"],
        "version": "seed_v0.1_synthetic_162",
        "notes": item["v1_notes"],
        "bucket_groups": bucket_groups,   # PL 요구: {score_5: [...], score_3: [...], score_0: [...]}
        "examples": flat_examples,        # v2.rag.GoldenSetRAG 호환
    }


def _rag_bucket(label: str) -> str:
    """PL 버킷 라벨 → RAG types 의 score_bucket 값으로 매핑."""
    if label == "full":
        return "full"
    if label == "zero":
        return "zero"
    return "partial"  # partial / partial_mid / partial_low 모두 partial 로


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------


def main() -> int:
    # 실행 가드 — PL 최종 확정(2026-04-20) 으로 생성 보류.
    # 향후 시니어 라벨링 워크샵 이후 재개 시 환경변수 1 설정.
    if os.environ.get("V2_SYNTHETIC_GOLDENSET_APPROVED") != "1":
        logger.error(
            "스크립트 보류 상태. Golden-set synthetic 162 생성은 PL 최종 확정으로 취소됨. "
            "재개하려면 PL 승인 후 V2_SYNTHETIC_GOLDENSET_APPROVED=1 환경변수 설정."
        )
        return 2

    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default="generic")
    parser.add_argument("--out", default=None, help="출력 디렉토리 (기본: v2/tenants/<tenant>/golden_set)")
    parser.add_argument(
        "--model",
        default=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0"),
        help="Bedrock Sonnet 4 모델 ID",
    )
    parser.add_argument("--per-bucket", type=int, default=3, help="bucket 당 예시 개수 (기본 3)")
    parser.add_argument("--items", default="", help="특정 item_number 만 (콤마 구분). 예: 1,2,15")
    parser.add_argument("--dry-run", action="store_true", help="LLM 호출 없이 프롬프트만 출력")
    parser.add_argument("--temperature", type=float, default=0.9)
    parser.add_argument("--max-tokens", type=int, default=2500)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    out_dir = Path(args.out) if args.out else (_PIPELINE_DIR / "v2" / "tenants" / args.tenant / "golden_set")
    out_dir.mkdir(parents=True, exist_ok=True)

    selected_items: list[dict]
    if args.items.strip():
        want = {int(x) for x in args.items.split(",") if x.strip()}
        selected_items = [it for it in ITEMS if it["item_number"] in want]
    else:
        selected_items = ITEMS

    logger.info(
        "Generating %d items × bucket × %d examples each (model=%s, out=%s)",
        len(selected_items),
        args.per_bucket,
        args.model,
        out_dir,
    )

    if not args.dry_run:
        llm = _get_llm(args.model, args.temperature, args.max_tokens)
    else:
        llm = None

    for item in selected_items:
        logger.info("== item #%d %s (%s) ==", item["item_number"], item["name"], item["category"])

        examples_by_score: dict[int, list[dict]] = {}
        for bucket_label, score, criteria in get_buckets_for(item):
            if args.dry_run:
                prompt = _user_prompt(item, bucket_label, score, criteria, args.per_bucket)
                logger.info("[dry-run] item=%d bucket=%s\n---\n%s\n---", item["item_number"], bucket_label, prompt[:400])
                examples_by_score[score] = []
                continue

            logger.info("  bucket=%s score=%d", bucket_label, score)
            try:
                examples = generate_bucket(llm, item, bucket_label, score, criteria, args.per_bucket)
            except Exception as e:
                logger.error("  FAILED bucket=%s: %s", bucket_label, e)
                examples = []
            # 같은 score 가 두 번 나올 수 있는 4-단계 항목은 append
            examples_by_score.setdefault(score, []).extend(examples)

        payload = build_file_payload(item, examples_by_score)
        target = out_dir / _filename_for(item)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("  wrote %s  (%d examples)", target.name, len(payload["examples"]))

    logger.info("Done. Output dir: %s", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
