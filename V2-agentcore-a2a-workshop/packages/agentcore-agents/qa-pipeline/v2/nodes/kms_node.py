# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
KMS 노드 — 2-step (인텐트 분류 → 인텐트별 KMS 평가).

위치: Layer 1 직후 *가장 먼저* 실행 (sub-agent 보다 선행).

흐름:
  [Step 1] 인텐트 set 분류 (Tool Use enum 강제)
     transcript → detected_intents = ["교환"] 또는 멀티 ["반품", "교환"]
  [Step 2] 인텐트별 KMS 평가 (각 인텐트마다 1회 LLM, 병렬)
     각 인텐트 + 해당 md 데이터 + transcript → tab_evaluations
  [출력] state["kms_evaluation"] = {
     available, detected_intents, classification_rationale,
     evaluations_by_intent, used_tabs, raw_outputs
  }

데이터 소스: `kms_data/kms_<intent>.md` (회원정보/환불/교환/반품/수선/배송/취소).
xlsx 직접 로딩은 openpyxl read-only mode 의 max_row 부정확 + 542KB 풀 로딩 hang 으로 폐기.
md 변환은 `kms_data/_convert_xlsx_to_md.py` 빌드 스크립트로 1회 수행.

환경변수:
  QA_KMS_DATA_DIR         — md 디렉토리 절대경로 (기본: 본 모듈과 같은 위치의 kms_data/)
  QA_KMS_NODE_DISABLED    — "1/true/yes" 면 노드 비활성 (placeholder 만 반환)
  QA_KMS_BEDROCK_MODEL_ID — LLM 모델 (기본 us.anthropic.claude-sonnet-4-6)
  QA_KMS_MAX_WORKERS      — Step 2 병렬 워커 수 (기본 5, Bedrock TPM 고려)
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ★ 2026-05-07: 프론트 모델 드롭다운 (state.bedrock_model_id) 을 KMS 호출에 전파.
# 이전엔 _get_model_id() 가 QA_KMS_BEDROCK_MODEL_ID env 만 보고 request override 무시.
# kms_node 진입 시 이 contextvar 에 stash → _bedrock_tool_use / _bedrock_json 이 read.
# ThreadPoolExecutor 워커도 부모 contextvar 를 자동 상속.
_REQUEST_MODEL_OVERRIDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "kms_request_model_override", default=None
)


# 인텐트 후보 — md 파일 이름 7종 (코오롱 KMS)
KMS_INTENT_TABS: tuple[str, ...] = (
    "회원정보",
    "환불",
    "교환",
    "반품",
    "수선",
    "배송",
    "취소",
)

# 모듈 레벨 캐시 — md 한 번만 로드
_TAB_CACHE: dict[str, str] | None = None
_CACHE_DIR: str | None = None


def _resolve_md_dir() -> Path:
    env_path = os.environ.get("QA_KMS_DATA_DIR", "").strip()
    if env_path:
        return Path(env_path)
    return Path(__file__).parent / "kms_data"


def _is_node_disabled() -> bool:
    raw = os.environ.get("QA_KMS_NODE_DISABLED", "").strip().lower()
    return raw in {"1", "true", "yes"}


def _get_max_workers() -> int:
    try:
        return max(1, int(os.environ.get("QA_KMS_MAX_WORKERS", "5")))
    except ValueError:
        return 5


# 모듈 레벨 LinearRAG 캐시 — 첫 호출 시 인덱싱, 이후 재사용
_LINEAR_RAG_INSTANCE: Any = None
_LINEAR_RAG_CORPUS_SIG: str | None = None  # 인덱싱 무효화 키 (md 디렉토리 mtime 합)


def _build_linear_rag_corpus_from_md(md_dir: Path) -> list[dict[str, Any]]:
    """kms_data/kms_<intent>.md → LinearRAG corpus item 리스트.

    xlsx 에서 변환된 md 가 단일 source of truth. md 의 `## [n] <branch>` 섹션 을 한 row 로 파싱.
    각 row 는 LinearRAG.kms_table_to_corpus 가 기대하는 dict 포맷:
       {"pid": str, "intent": str, "branch": str, "condition": str,
        "required_keywords": list[str], "required_statements": list[str]}

    (xlsx 직접 로딩은 read_only=True hang 이슈로 폐기 — md 만 사용.)
    """
    if not md_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for intent in KMS_INTENT_TABS:
        md_path = md_dir / f"kms_{intent}.md"
        if not md_path.exists():
            continue
        try:
            text = md_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("LinearRAG corpus md 로드 실패 (%s): %s", md_path.name, e)
            continue
        rows.extend(_parse_kms_md(text, intent))
    return rows


def _parse_kms_md(text: str, intent: str) -> list[dict[str, Any]]:
    """단일 인텐트 md → row list. ## [n] <branch> 단위 파싱."""
    rows: list[dict[str, Any]] = []
    # 섹션 split — `## [` 헤더 단위
    section_re = re.compile(r"^##\s+\[(\d+)\]\s+(.+?)$", re.MULTILINE)
    sections: list[tuple[int, str, int]] = []  # (idx, branch, start_pos)
    for m in section_re.finditer(text):
        sections.append((int(m.group(1)), m.group(2).strip(), m.end()))
    if not sections:
        return rows
    # 각 섹션의 본문 = 다음 섹션 헤더 직전까지
    for i, (idx, branch, start) in enumerate(sections):
        end = sections[i + 1][2] - len(sections[i + 1][1]) - 10 if i + 1 < len(sections) else len(text)
        body = text[start:end]
        condition = ""
        keywords: list[str] = []
        statements: list[str] = []
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- **조건**:"):
                condition = stripped.replace("- **조건**:", "").strip()
            elif stripped.startswith("- **필수 키워드**:"):
                kw = stripped.replace("- **필수 키워드**:", "").strip()
                keywords = [k.strip() for k in re.split(r"[,/]", kw) if k.strip()]
            elif stripped.startswith("- **필수 안내 사항**:"):
                # 이후 라인의 들여쓰기 - 항목들
                continue
            elif stripped.startswith("- ") and not stripped.startswith("- **"):
                # 들여쓰기 - 항목 (필수 안내 사항)
                statements.append(stripped[2:].strip())
        rows.append({
            "pid": f"{intent}_{branch}_{idx}",
            "intent": intent,
            "branch": branch,
            "condition": condition,
            "required_keywords": keywords,
            "required_statements": statements,
        })
    return rows


def _get_model_id() -> str:
    """모델 우선순위: request override (frontend dropdown) → QA_KMS_BEDROCK_MODEL_ID env → 기본값.
    request override 는 kms_node 진입 시 contextvar 에 set 되며 ThreadPool 워커도 상속함.
    """
    override = _REQUEST_MODEL_OVERRIDE.get()
    if override:
        return override
    return os.environ.get("QA_KMS_BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")


def _load_md_tabs(md_dir: Path) -> dict[str, str] | None:
    """인텐트별 md 파일 7종을 로드해 prompt-ready 텍스트 dict 로 반환 (캐시).

    Step 1 (인텐트 분류) 결과로 검출된 인텐트만 Step 2 에서 해당 md 텍스트를 읽음.
    파일 이름 규칙: kms_<intent>.md (intent ∈ KMS_INTENT_TABS).
    """
    global _TAB_CACHE, _CACHE_DIR

    if _TAB_CACHE is not None and _CACHE_DIR == str(md_dir):
        return _TAB_CACHE

    if not md_dir.exists() or not md_dir.is_dir():
        logger.warning("KMS md 디렉토리 부재: %s", md_dir)
        return None

    tabs: dict[str, str] = {}
    for intent in KMS_INTENT_TABS:
        md_path = md_dir / f"kms_{intent}.md"
        if not md_path.exists():
            logger.warning("KMS md 파일 부재 — skip: %s", md_path.name)
            continue
        try:
            text = md_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning("KMS md 로드 실패 (%s): %s", md_path.name, e)
            continue
        if text:
            tabs[intent] = text

    _TAB_CACHE = tabs
    _CACHE_DIR = str(md_dir)
    total_chars = sum(len(v) for v in tabs.values())
    logger.info("KMS md 로드 완료: %s (인텐트 %d개, 총 %d자)",
                md_dir.name, len(tabs), total_chars)
    return tabs


def _build_single_tab_text(intent: str, md_text: str) -> str:
    """단일 인텐트 md 텍스트 → prompt 섹션 (md 그대로 사용).

    md 파일이 이미 prompt-ready 포맷 (헤더/리스트) 으로 빌드돼 있으므로 wrapping 만 추가.
    """
    return f"### [{intent}] 평가 기준\n{md_text}"


def _bedrock_tool_use(prompt: str, tool_def: dict, max_tokens: int = 2000) -> dict:
    """Bedrock Tool Use → 도구 입력 JSON 반환. 실패 시 {}."""
    import boto3

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    model = _get_model_id()
    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        inference_cfg: dict[str, Any] = {"maxTokens": max_tokens}
        if "opus" not in model.lower():
            inference_cfg["temperature"] = 0.0
        resp = client.converse(
            modelId=model,
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
    except Exception as e:
        logger.warning("KMS Tool Use 호출 실패: %s", e)
        return {"_error": str(e)}


def _bedrock_json(prompt: str, max_tokens: int = 3000) -> dict:
    """Bedrock 일반 호출 → JSON 파싱. 실패 시 {_error}."""
    import boto3

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    model = _get_model_id()
    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        inference_cfg: dict[str, Any] = {"maxTokens": max_tokens}
        if "opus" not in model.lower():
            inference_cfg["temperature"] = 0.0
        resp = client.converse(
            modelId=model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig=inference_cfg,
        )
        text = resp["output"]["message"]["content"][0]["text"]
    except Exception as e:
        logger.warning("KMS LLM 호출 실패: %s", e)
        return {"_error": str(e)}

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {"_raw": text, "_error": "no_json"}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        return {"_raw": text, "_error": f"json_decode: {e}"}


# ---------------------------------------------------------------------------
# LinearRAG 인덱스 lazy init + 인텐트 분류 (대안 모드)
# ---------------------------------------------------------------------------


def _ensure_linear_rag_index() -> Any:
    """LinearRAG 인덱스 lazy 빌드 (모듈 레벨 캐시).

    md 디렉토리 내용 변경 시 (mtime) 자동 재빌드. 첫 호출 시 30~120초 소요
    (Bedrock Titan v2 embedding + Korean NER). 이후 재사용.
    """
    global _LINEAR_RAG_INSTANCE, _LINEAR_RAG_CORPUS_SIG

    md_dir = _resolve_md_dir()
    # 시그니처 = md 파일들의 mtime 합 (변경 감지)
    md_files = sorted(md_dir.glob("kms_*.md")) if md_dir.exists() else []
    sig = "|".join(f"{p.name}:{int(p.stat().st_mtime)}" for p in md_files)

    if _LINEAR_RAG_INSTANCE is not None and _LINEAR_RAG_CORPUS_SIG == sig:
        return _LINEAR_RAG_INSTANCE

    rows = _build_linear_rag_corpus_from_md(md_dir)
    if not rows:
        logger.warning("LinearRAG: corpus 비어있음 (md_dir=%s)", md_dir)
        return None

    try:
        from v2.rag.linear_rag import LinearRAG, build_index, kms_table_to_corpus
        import boto3
    except ImportError as e:
        logger.warning("LinearRAG 의존성 없음 — %s", e)
        return None

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    bedrock_client = boto3.client("bedrock-runtime", region_name=region)
    embed_cache: dict[str, tuple[float, ...]] = {}

    def embed_fn(text: str):
        if not text or not text.strip():
            return None
        if text in embed_cache:
            return embed_cache[text]
        try:
            resp = bedrock_client.invoke_model(
                modelId="amazon.titan-embed-text-v2:0",
                contentType="application/json",
                accept="application/json",
                body=json.dumps({"inputText": text[:8000], "dimensions": 1024, "normalize": True}),
            )
            payload = json.loads(resp["body"].read())
            vec = payload.get("embedding")
            if not vec:
                return None
            t = tuple(vec)
            embed_cache[text] = t
            return t
        except Exception as e:
            logger.warning("LinearRAG embed 실패: %s", e)
            return None

    # 인덱스 저장 위치 — TEMP/qa_kms_linear_rag (사용자별)
    import tempfile
    tenant_root = Path(tempfile.gettempdir()) / "qa_kms_linear_rag"
    tenant_root.mkdir(parents=True, exist_ok=True)
    tenant_id = "kms_intent"

    # ★ 디스크 인덱스 존재 + corpus 시그니처 stamp 일치 시 build_index 스킵 (재사용)
    sig_file = tenant_root / f"{tenant_id}_md_sig.txt"
    try:
        from v2.rag.linear_rag.tri_graph import tri_graph_exists
        cached_sig = sig_file.read_text(encoding="utf-8").strip() if sig_file.exists() else ""
        index_exists = tri_graph_exists(tenant_id, tenant_root)
    except Exception:
        cached_sig = ""
        index_exists = False

    if index_exists and cached_sig == sig:
        logger.info("LinearRAG 인덱스 디스크 재사용 — sig match, build skip (tenant_root=%s)", tenant_root)
        try:
            rag = LinearRAG(tenant_id=tenant_id, tenant_root=tenant_root, embed_fn=embed_fn)
            _LINEAR_RAG_INSTANCE = rag
            _LINEAR_RAG_CORPUS_SIG = sig
            return rag
        except Exception as e:
            logger.warning("디스크 인덱스 로드 실패 — 재빌드: %s", e)

    logger.info("LinearRAG 인덱싱 시작 — corpus rows=%d, tenant_root=%s", len(rows), tenant_root)
    try:
        corpus = kms_table_to_corpus(rows)
        # ★ 시그니처 확인 (indexer.build_index): tenant_id / corpus / tenant_root / embed_fn (keyword-only)
        build_result = build_index(
            tenant_id=tenant_id,
            corpus=corpus,
            tenant_root=tenant_root,
            embed_fn=embed_fn,
        )
        # IndexBuildResult 구조 — 디스크에 영속화됨. LinearRAG 인스턴스는 lazy load 로 graph 읽음.
        logger.info("LinearRAG 인덱싱 완료 — build_result=%s", type(build_result).__name__)
        # 시그니처 stamp 기록 — 다음 프로세스에서 재사용 판정
        try:
            sig_file.write_text(sig, encoding="utf-8")
        except Exception:
            pass
        rag = LinearRAG(
            tenant_id=tenant_id,
            tenant_root=tenant_root,
            embed_fn=embed_fn,
        )
    except Exception as e:
        logger.exception("LinearRAG 인덱싱 실패: %s", e)
        return None

    _LINEAR_RAG_INSTANCE = rag
    _LINEAR_RAG_CORPUS_SIG = sig
    return rag


def _detect_intents_via_linear_rag(transcript: str) -> dict[str, Any]:
    """LinearRAG (Tri-Graph) 기반 인텐트 set 분류 — LLM 모드의 alternative.

    실험 결과 (F1=0.435 vs LLM 0.933): 정확도 낮음. 사용자가 명시적으로 선택할 때만 활성.
    교환·반품·수선 으로 over-detect 경향.
    """
    rag = _ensure_linear_rag_index()
    if rag is None:
        return {
            "intents": [],
            "rationale": "LinearRAG 인덱스 빌드 실패 — md corpus / Bedrock embedding 확인",
            "_error": "linear_rag_unavailable",
        }

    query = (transcript or "")[:7800]
    if not query:
        return {"intents": [], "rationale": "transcript 비어있음"}

    try:
        result = rag.retrieve(query, top_k=10)
    except Exception as e:
        logger.warning("LinearRAG retrieve 실패: %s", e)
        return {"intents": [], "rationale": f"retrieve 실패: {e}", "_error": str(e)}

    # passage 별 ppr_score 를 인텐트별 합산
    intent_scores: dict[str, float] = {}
    passages_meta: list[dict[str, Any]] = []
    for p in result.passages:
        intent = (p.metadata or {}).get("intent")
        if intent and intent in KMS_INTENT_TABS:
            intent_scores[intent] = intent_scores.get(intent, 0.0) + p.ppr_score
        passages_meta.append({
            "pid": p.pid,
            "intent": intent,
            "branch": (p.metadata or {}).get("branch"),
            "ppr_score": round(p.ppr_score, 4),
        })

    if not intent_scores:
        return {
            "intents": [],
            "rationale": "LinearRAG retrieve 결과에서 인텐트 metadata 미검출",
            "linear_rag_passages": passages_meta,
        }

    threshold_ratio = float(os.environ.get("QA_KMS_LINEAR_RAG_THRESHOLD", "0.3"))
    max_score = max(intent_scores.values())
    threshold = max_score * threshold_ratio
    detected = sorted(
        [(i, s) for i, s in intent_scores.items() if s >= threshold],
        key=lambda x: x[1],
        reverse=True,
    )
    intents = [i for i, _ in detected]

    rationale = (
        f"LinearRAG 분류 — top_k=10 retrieve 후 인텐트별 ppr_score 합산 → "
        f"max×{threshold_ratio} 이상 검출. 점수: "
        + ", ".join(f"{i}={round(s, 3)}" for i, s in sorted(intent_scores.items(), key=lambda x: -x[1]))
    )

    return {
        "intents": intents,
        "rationale": rationale,
        "linear_rag_scores": {i: round(s, 4) for i, s in intent_scores.items()},
        "linear_rag_passages": passages_meta,
    }


# ---------------------------------------------------------------------------
# Step 1 — 인텐트 set 분류
# ---------------------------------------------------------------------------


def _detect_intents(transcript: str) -> dict[str, Any]:
    """transcript → detected_intents (set). Tool Use enum 강제.

    return:
        {"intents": ["교환"], "rationale": "..."}
        실패 시 {"intents": [], "rationale": "...", "_error": ...}
    """
    tool_def = {
        "name": "emit_intent_set",
        "description": (
            "전체 transcript 에서 *실제 처리가 진행된* 인텐트 set 을 반환. "
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
                            "enum": list(KMS_INTENT_TABS),
                        },
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["intents", "rationale"],
            }
        },
    }

    # ★ 실험 검증 prompt (2026-05-06 LinearRAG 비교 실험에서 F1=0.933, Recall=100% 달성).
    # 추가 over-detection 방지 규칙 (원복/이전 통화/확인 약속만) 은 false-negative 유발해 제외.
    prompt = f"""다음 코오롱 CS 상담 transcript 에서 **실제 처리가 진행된 인텐트** 만 set 으로 검출.

인텐트 후보 (7 종):
- 교환 / 반품 / 배송 / 수선 / 취소 / 환불 / 회원정보

판단 기준:
- ✅ 검출 — 고객이 요청 + 상담사가 안내 / 접수 진행
- ❌ 비검출 — 단순 언급만 있고 실제 처리 없음
- ❌ 비검출 — 외부 구매처 (지마켓/11번가 등) 만 안내하고 종료 → intents=[]
- ✅ 멀티 — 한 transcript 안 두 인텐트 모두 처리되면 둘 다

⚠ 환불 인텐트 특별 규칙 — over-detection 방지:
- ❌ 비검출 — 반품/취소의 마지막 단계 "결제수단 환불 영업일 3-5일" 자동 안내
- ✅ 검출 — 환불 단독 처리 (가상계좌 환불 변경, 이전 건 환불 일정 문의 등)
- 일반 패턴: 반품 = 반품+환불 모두 포함, 환불 별도 X
- 일반 패턴: 취소 = 취소+환불 모두 포함, 환불 별도 X

예시:
- "티셔츠 반품 접수 완료, 환불 영업일 3-5일" → ["반품"]
- "주문 취소 완료, 결제수단 환불" → ["취소"]
- "이전에 반품한 건 환불 언제 되나요?" → ["환불"]
- "교환 가능하지만 외부 구매라 처리 불가" → []
- "반품 접수 → 그러면 교환으로 변경" → ["반품", "교환"]
- "배송지 변경 처리 완료" → ["배송"]

Transcript:
{transcript}

emit_intent_set 도구로 검출 결과 출력."""

    result = _bedrock_tool_use(prompt, tool_def, max_tokens=2000)
    if "_error" in result:
        return {"intents": [], "rationale": "", "_error": result.get("_error")}

    intents = result.get("intents") or []
    if not isinstance(intents, list):
        intents = []
    intents = [i for i in intents if i in KMS_INTENT_TABS]
    # 중복 제거 (순서 유지)
    seen: set[str] = set()
    deduped: list[str] = []
    for i in intents:
        if i not in seen:
            seen.add(i)
            deduped.append(i)

    return {
        "intents": deduped,
        "rationale": result.get("rationale", ""),
    }


# ---------------------------------------------------------------------------
# Step 2 — 인텐트별 KMS 평가
# ---------------------------------------------------------------------------


def _evaluate_intent(intent: str, md_text: str, transcript: str) -> dict[str, Any]:
    """한 인텐트의 md 데이터로 transcript 평가 (10점 만점 + 근거)."""
    tab_text = _build_single_tab_text(intent, md_text)
    prompt = f"""다음 코오롱 CS 상담 transcript 가 [{intent}] 인텐트의 평가 기준을 충족하는지 평가.

{tab_text}

상담 transcript:
{transcript}

=== 작업 ===
1. 위 [{intent}] 평가 기준의 각 세부사항 (분기) 에 대해, transcript 가 *필수 키워드* + *필수 안내 사항* 을 충족하는지 평가.
2. 적용되는 분기만 평가 (조건 미해당이면 skip).
3. 인텐트 *전체* 에 대한 종합 점수 0~10점 부여.
4. 점수 산정 근거 (reasoning) 명확히 기술.

=== 점수 기준 (10점 만점) ===
- 10점: 모든 필수 키워드 + 안내 사항 완벽 충족. 누락 0건.
- 8~9점: 주요 키워드/안내 모두 충족, 일부 사소한 누락.
- 6~7점: 핵심 키워드/안내는 충족하나 중요 항목 1~2개 누락.
- 4~5점: 절반 정도 충족, 중요 누락 다수.
- 1~3점: 대부분 누락, 형식적 안내만 진행.
- 0점: 평가 기준 전혀 미충족 또는 처리 자체가 잘못됨.

JSON 만 출력 (다른 말 X):
{{
  "score": 8,
  "reasoning": "점수 산정 근거 (2~4문장). 어떤 항목을 충족했고 무엇이 누락됐는지 구체적으로.",
  "applied_branches": ["적용된 세부사항 목록"],
  "tab_evaluations": [
    {{
      "branch": "세부사항 (예: 무료/유료)",
      "satisfied_keywords": ["..."],
      "missing_keywords": ["..."],
      "satisfied_statements": ["..."],
      "missing_statements": ["..."],
      "evidence": ["짧은 발화 인용 — 점수의 근거"]
    }}
  ],
  "summary": "한 줄 요약"
}}"""

    result = _bedrock_json(prompt, max_tokens=3500)
    if "_error" in result:
        return {
            "score": None,
            "reasoning": "",
            "applied_branches": [],
            "tab_evaluations": [],
            "summary": "",
            "_error": result.get("_error"),
        }
    # score 정규화 — 0~10 범위 내 정수/실수만 허용
    raw_score = result.get("score")
    score: float | None = None
    try:
        if raw_score is not None:
            score = max(0.0, min(10.0, float(raw_score)))
    except (TypeError, ValueError):
        score = None
    return {
        "score": score,
        "reasoning": result.get("reasoning", ""),
        "applied_branches": result.get("applied_branches") or [],
        "tab_evaluations": result.get("tab_evaluations") or [],
        "summary": result.get("summary", ""),
    }


def _detect_mismatches(intent: str, md_text: str, transcript: str) -> dict[str, Any]:
    """[{intent}] KMS 와 상담사 발화 비교 — 오안내 (mismatch) 검출.

    _evaluate_intent 가 *누락* (안 한 안내) 을 평가하므로 여기서는 제외.
    *상담사가 안내한 내용이 KMS 정답과 다른 경우* 만 추출 → 환각 검증 후 반환.

    환각 방지:
      - LLM 이 KMS 인용 (kms_quote) + 상담사 인용 (agent_quote) 둘 다 *원문 verbatim* 으로 출력
      - postprocess 에서 두 인용이 진짜 원문에 substring 으로 존재하는지 검증
      - 검증 실패 → rejected_mismatches 로 분리, mismatches 에서 제거
    """
    tab_text = _build_single_tab_text(intent, md_text)
    prompt = f"""다음 코오롱 CS 상담에서, 상담사가 안내한 내용 중 [{intent}] KMS 와 *다르게* 안내한 부분을 검출.

{tab_text}

상담 transcript:
{transcript}

=== 작업 ===
상담사가 실제로 안내한 사실 (수치, 기간, 조건, 절차) 중 KMS 의 표준 답변과 *다른* 것만 추출.

=== 중요 규칙 (환각 방지) ===
1. KMS 에 명시된 사실만 비교 대상. KMS 에 없는 내용은 mismatch 아님.
2. 상담사가 *언급조차 안 한* 항목은 *누락* 이지 오안내 아님 — 여기서 제외.
3. KMS 와 상담사 발화 둘 다 *원문 그대로* 인용 (표현/어미 다듬지 말 것).
4. 인용은 transcript / KMS 에 *글자 단위로 존재* 해야 함. 없으면 출력하지 마라.
5. 표현은 다르나 의미가 같은 경우 (예: "한 달" = "30일") → mismatch 아님.
6. 확실치 않으면 출력하지 마라 — false positive 가 가장 큰 문제.

JSON 만 출력 (다른 말 X):
{{
  "mismatches": [
    {{
      "fact_label": "환불 기간",
      "kms_value": "영업일 기준 3-5일",
      "agent_value": "3일",
      "kms_quote": "카드 취소는 영업일 기준 3-5일 소요되는 점 양해 부탁드립니다.",
      "agent_quote": "환불은 3일 안에 들어가요",
      "severity": "high",
      "reasoning": "KMS 는 3-5영업일이나 상담사는 3일로 단언 — 고객 오인 가능"
    }}
  ],
  "no_mismatch_reason": "mismatches 가 비어있는 경우 한 줄 사유 — '상담사가 KMS 사실을 정확히 안내' 또는 'KMS 사실에 대한 상담사 발화 없음'"
}}"""

    result = _bedrock_json(prompt, max_tokens=3000)
    if "_error" in result:
        return {
            "mismatches": [],
            "rejected_mismatches": [],
            "no_mismatch_reason": "",
            "_error": result.get("_error"),
        }

    raw_mismatches = result.get("mismatches") or []
    if not isinstance(raw_mismatches, list):
        raw_mismatches = []

    # 환각 검증 — 인용 substring 체크 (공백 정규화 후)
    md_norm = re.sub(r"\s+", "", md_text or "")
    transcript_norm = re.sub(r"\s+", "", transcript or "")

    verified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for m in raw_mismatches:
        if not isinstance(m, dict):
            continue
        kms_quote = (m.get("kms_quote") or "").strip()
        agent_quote = (m.get("agent_quote") or "").strip()
        kms_quote_norm = re.sub(r"\s+", "", kms_quote)
        agent_quote_norm = re.sub(r"\s+", "", agent_quote)

        kms_found = bool(kms_quote_norm) and kms_quote_norm in md_norm
        agent_found = bool(agent_quote_norm) and agent_quote_norm in transcript_norm

        if kms_found and agent_found:
            verified.append({**m, "verified": True})
        else:
            reason = []
            if not kms_quote_norm:
                reason.append("kms_quote empty")
            elif not kms_found:
                reason.append("kms_quote not in md")
            if not agent_quote_norm:
                reason.append("agent_quote empty")
            elif not agent_found:
                reason.append("agent_quote not in transcript")
            rejected.append(
                {**m, "verified": False, "reject_reason": "; ".join(reason) or "unknown"}
            )

    return {
        "mismatches": verified,
        "rejected_mismatches": rejected,
        "no_mismatch_reason": result.get("no_mismatch_reason", ""),
    }


# ---------------------------------------------------------------------------
# 노드 진입점
# ---------------------------------------------------------------------------


def kms_node(state: dict[str, Any]) -> dict[str, Any]:
    """KMS 노드 — 2-step (인텐트 분류 → 인텐트별 KMS 평가).

    Layer 1 (preprocessing) 직후 *가장 먼저* 실행되는 노드.
    입력: state["transcript"] = STT 원문 전체 (Layer 1 가 변경하지 않고 보존).
    KMS 는 인텐트 분류 + 평가 모두 *원문 전체* 를 LLM 에 그대로 전달.

    출력 state 키:
      kms_evaluation: {
        available: bool,
        reason?: str,
        detected_intents: [str, ...],          # Step 1 결과
        classification_rationale: str,
        evaluations_by_intent: {                # Step 2 결과
          intent: {
            score, reasoning, applied_branches, tab_evaluations, summary,  # 충족도
            mismatches, rejected_mismatches, no_mismatch_reason,           # 오안내
          }
        },
        mismatches_summary: {                   # UI/대시보드용 종합
          total_verified: int,
          total_rejected: int,
          by_severity: {high, medium, low},
        },
        used_tabs: [str, ...],
      }
    """
    # ★ 진입 로그 (hang 위치 즉시 식별용 — 진입 자체가 안 되면 로그 부재로 즉시 진단 가능)
    transcript_preview = (state.get("transcript") or "")[:60].replace("\n", " ")
    logger.info(
        "kms_node ENTER: state_keys=%d transcript_chars=%d preview='%s...'",
        len(state.keys()) if isinstance(state, dict) else -1,
        len(state.get("transcript") or "") if isinstance(state, dict) else -1,
        transcript_preview,
    )

    # ★ 2026-05-07: 프론트 모델 드롭다운 (state.bedrock_model_id) 을 KMS LLM 호출에 전파.
    # contextvar 로 set → ThreadPool 워커도 상속 → _get_model_id() 가 read.
    request_model = state.get("bedrock_model_id") if isinstance(state, dict) else None
    if request_model:
        _REQUEST_MODEL_OVERRIDE.set(str(request_model))
        logger.info("kms_node: request model override = %s", request_model)

    # 1. 비활성 토글
    if _is_node_disabled():
        logger.info("kms_node: 비활성 (env QA_KMS_NODE_DISABLED) — placeholder 반환")
        return {
            "kms_evaluation": {
                "available": False,
                "reason": "node_disabled",
                "detected_intents": [],
                "evaluations_by_intent": {},
            }
        }

    # 2. md 데이터 로딩 (인텐트 후보 7종)
    md_dir = _resolve_md_dir()
    logger.info("kms_node: md 로딩 시도 — %s (exists=%s)", md_dir, md_dir.exists())
    tabs = _load_md_tabs(md_dir)
    if not tabs:
        logger.warning("kms_node: md 로딩 실패 — 노드 비활성 반환 (dir=%s)", md_dir)
        return {
            "kms_evaluation": {
                "available": False,
                "reason": f"md_unavailable: {md_dir}",
                "detected_intents": [],
                "evaluations_by_intent": {},
            }
        }

    # 3. transcript
    transcript = state.get("transcript") or ""
    if not transcript:
        preprocessing = state.get("preprocessing") or {}
        turns = preprocessing.get("turns") or []
        if turns:
            transcript = "\n".join(
                f"{t.get('speaker', '?')}: {t.get('text', '')}" for t in turns
            )
    if not transcript:
        return {
            "kms_evaluation": {
                "available": False,
                "reason": "no_transcript",
                "detected_intents": [],
                "evaluations_by_intent": {},
            }
        }

    # 라이브 SSE 콜백 — server_v2 가 _debate_on_event 로 주입한 callable.
    # debate 전용이 아니라 generic event channel (KMS / 기타 노드도 재사용).
    _on_event = state.get("_debate_on_event")

    def _emit(name: str, payload: dict[str, Any]) -> None:
        if not callable(_on_event):
            return
        try:
            _on_event(name, payload)
        except Exception:  # pragma: no cover — 이벤트 발행 실패가 평가 중단시키면 안 됨
            logger.exception("kms_node emit %s failed", name)

    # 4. Step 1 — 인텐트 분류 (mode 분기: "llm" 기본 / "linear_rag" 대안)
    intent_mode = (state.get("kms_intent_mode") or os.environ.get("QA_KMS_INTENT_MODE", "llm")).strip().lower()
    if intent_mode not in ("llm", "linear_rag"):
        intent_mode = "llm"
    logger.info(
        "kms_node Step 1: 인텐트 분류 시작 (mode=%s, transcript_len=%d)",
        intent_mode, len(transcript),
    )
    if intent_mode == "linear_rag":
        classification = _detect_intents_via_linear_rag(transcript)
    else:
        classification = _detect_intents(transcript)
    detected_intents: list[str] = classification.get("intents") or []
    classification_rationale = classification.get("rationale", "")

    # ★ 2026-05-07: 인텐트 분류 즉시 SSE 푸시 → 프론트가 KMS 노드 sub 라이브 갱신.
    _emit(
        "kms_intent_detected",
        {
            "node_id": "kms",
            "detected_intents": list(detected_intents),
            "intent_mode": intent_mode,
            "rationale": classification_rationale[:300] if classification_rationale else "",
        },
    )

    if "_error" in classification:
        return {
            "kms_evaluation": {
                "available": False,
                "reason": f"classification_error: {classification.get('_error')}",
                "detected_intents": [],
                "classification_rationale": classification_rationale,
                "evaluations_by_intent": {},
                "used_tabs": list(tabs.keys()),
            }
        }

    logger.info("kms_node Step 1 결과: detected=%s", detected_intents)

    # 5. Step 2 — 인텐트별 평가 (병렬)
    evaluations_by_intent: dict[str, dict[str, Any]] = {}

    # 외부구매 / 빈 검출 case → Step 2 skip
    if not detected_intents:
        return {
            "kms_evaluation": {
                "available": True,
                "detected_intents": [],
                "classification_rationale": classification_rationale,
                "evaluations_by_intent": {},
                "used_tabs": list(tabs.keys()),
                "reason": "no_intent_detected (외부구매 / 처리 X)",
            }
        }

    # 각 인텐트 md 매칭 → 병렬 평가
    intents_with_tabs = [(i, tabs[i]) for i in detected_intents if i in tabs]
    intents_without_tabs = [i for i in detected_intents if i not in tabs]
    if intents_without_tabs:
        logger.warning("kms_node: 일부 인텐트의 md 미존재 — %s", intents_without_tabs)
    logger.info("kms_node Step 2: 병렬 평가 시작 — %d 인텐트", len(intents_with_tabs))

    if intents_with_tabs:
        # 인텐트당 2회 LLM 호출 (eval + mismatch) → 동일 풀에서 병렬 실행
        max_workers = min(_get_max_workers(), len(intents_with_tabs) * 2)
        # 인텐트별 결과 컨테이너 초기화
        for intent, _md in intents_with_tabs:
            evaluations_by_intent[intent] = {
                "score": None,
                "reasoning": "",
                "applied_branches": [],
                "tab_evaluations": [],
                "summary": "",
                "mismatches": [],
                "rejected_mismatches": [],
                "no_mismatch_reason": "",
            }

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: dict[Any, tuple[str, str]] = {}
            for intent, md_text in intents_with_tabs:
                futures[pool.submit(_evaluate_intent, intent, md_text, transcript)] = (
                    "eval",
                    intent,
                )
                futures[pool.submit(_detect_mismatches, intent, md_text, transcript)] = (
                    "mismatch",
                    intent,
                )

            for fut in as_completed(futures):
                kind, intent = futures[fut]
                try:
                    res = fut.result()
                    if kind == "eval":
                        for k in (
                            "score",
                            "reasoning",
                            "applied_branches",
                            "tab_evaluations",
                            "summary",
                        ):
                            if k in res:
                                evaluations_by_intent[intent][k] = res[k]
                        if "_error" in res:
                            evaluations_by_intent[intent]["_error_eval"] = res["_error"]
                        # ★ 2026-05-07: 점수 산출 즉시 SSE 푸시 → KMS 노드 sub 라이브 갱신.
                        _emit(
                            "kms_score_progress",
                            {
                                "node_id": "kms",
                                "intent": intent,
                                "score": res.get("score"),
                                "applied_branches": list(res.get("applied_branches") or []),
                            },
                        )
                    else:  # mismatch
                        evaluations_by_intent[intent]["mismatches"] = res.get(
                            "mismatches", []
                        )
                        evaluations_by_intent[intent]["rejected_mismatches"] = res.get(
                            "rejected_mismatches", []
                        )
                        evaluations_by_intent[intent]["no_mismatch_reason"] = res.get(
                            "no_mismatch_reason", ""
                        )
                        if "_error" in res:
                            evaluations_by_intent[intent]["_error_mismatch"] = res[
                                "_error"
                            ]
                except Exception as e:
                    logger.warning(
                        "kms_node Step 2 %s fail intent=%s: %s", kind, intent, e
                    )
                    err_key = "_error_eval" if kind == "eval" else "_error_mismatch"
                    evaluations_by_intent[intent][err_key] = str(e)

    # md 부재 인텐트는 빈 결과로 명시
    for intent in intents_without_tabs:
        evaluations_by_intent[intent] = {
            "applied_branches": [],
            "tab_evaluations": [],
            "summary": "",
            "_error": f"md_missing: {intent}",
        }

    # 오안내 종합 — UI/대시보드용 요약
    total_verified = 0
    total_rejected = 0
    by_severity: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for ev in evaluations_by_intent.values():
        verified_list = ev.get("mismatches") or []
        rejected_list = ev.get("rejected_mismatches") or []
        total_verified += len(verified_list)
        total_rejected += len(rejected_list)
        for m in verified_list:
            sev = (m.get("severity") or "").strip().lower()
            if sev in by_severity:
                by_severity[sev] += 1

    logger.info(
        "kms_node DONE: detected=%s, evaluated=%d, mismatches verified=%d rejected=%d",
        detected_intents,
        len(evaluations_by_intent),
        total_verified,
        total_rejected,
    )

    out: dict[str, Any] = {
        "available": True,
        "intent_mode": intent_mode,
        "detected_intents": detected_intents,
        "classification_rationale": classification_rationale,
        "evaluations_by_intent": evaluations_by_intent,
        "mismatches_summary": {
            "total_verified": total_verified,
            "total_rejected": total_rejected,
            "by_severity": by_severity,
        },
        "used_tabs": list(tabs.keys()),
    }
    # LinearRAG 모드일 때 디버그용 점수/passage 도 함께 노출
    if intent_mode == "linear_rag":
        if "linear_rag_scores" in classification:
            out["linear_rag_scores"] = classification["linear_rag_scores"]
        if "linear_rag_passages" in classification:
            out["linear_rag_passages"] = classification["linear_rag_passages"]
    return {"kms_evaluation": out}


async def kms_node_async(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 가 async 호출 시 호환용 wrapper."""
    return kms_node(state)
