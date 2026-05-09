# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cohere Rerank 3.5 (Bedrock) 통합 — RAG 후처리 재정렬.

호출 패턴:
    from v2.rag.reranker import is_reranker_enabled, rerank

    if is_reranker_enabled():
        order, ok = rerank(query, [doc.text for doc in candidates], top_n=5)
        # order = [(original_index, relevance_score), ...] desc
        # ok=True 이면 정상 호출 성공, False 면 폴백 (입력 순서 / score=0.0)
        if ok:
            candidates = [candidates[i] for i, _ in order]

활성화:
  - 환경변수 ``QA_RERANKER_ENABLED=true`` (기본 false)
  - 요청 시점에 ``set_reranker_enabled(True)`` contextvar 로 동적 ON/OFF — 프론트 토글 경로

Region:
  - Bedrock Rerank API 는 us-east-1 / us-west-2 / ca-central-1 / eu-central-1 / ap-northeast-1
  - ``AWS_REGION`` 자동 폴백, ``QA_RERANKER_REGION`` 으로 override 가능

Graceful degradation:
  - 호출 실패 / 클라이언트 미준비 시 입력 순서대로 top_n 반환 (점수 0.0)
  - 사용자 평가가 멈추지 않게 절대 raise 하지 않음
"""

from __future__ import annotations

import contextvars
import logging
import os
from datetime import datetime, timezone
from typing import Any


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Activation toggles
# ---------------------------------------------------------------------------

_RERANKER_ENABLED: contextvars.ContextVar[bool | None] = contextvars.ContextVar(
    "qa_reranker_enabled", default=None
)


def _env_default() -> bool:
    return os.getenv("QA_RERANKER_ENABLED", "false").strip().lower() in (
        "true", "1", "yes", "on",
    )


def is_reranker_enabled() -> bool:
    """Reranker 활성화 여부. contextvar 우선, 없으면 환경변수."""
    v = _RERANKER_ENABLED.get()
    if v is None:
        return _env_default()
    return bool(v)


def set_reranker_enabled(value: bool) -> contextvars.Token:
    """Reranker 활성 contextvar set. 반환 토큰은 reset 에 사용."""
    return _RERANKER_ENABLED.set(bool(value))


def reset_reranker_enabled(token: contextvars.Token) -> None:
    _RERANKER_ENABLED.reset(token)


# ---------------------------------------------------------------------------
# Provider 선택 (cohere vs llm) — 2026-05-08
#
# 동기:
#   Cohere Rerank 는 Q&A 학습 모델이라 "유사 평가 패턴 사례 retrieval" 과 task 미스매치.
#   LLM (Haiku) 으로 reranker 를 교체하면 자연어로 task 정의 가능 → fit ↑.
#   둘 다 운영 가능하게 provider 토글 도입 — A/B 비교 + 점진적 마이그레이션.
#
# Provider 종류:
#   - "cohere"  : Cohere Rerank 3.5 (Bedrock) — 빠름, 싸다, Q&A 학습
#   - "llm"     : Haiku 4.5 (Bedrock InvokeModel) — task fit ↑, 비용 ~3x, latency ~3-4x
#
# 활성화:
#   - 환경변수 ``QA_RERANKER_PROVIDER`` (기본 "cohere")
#   - 요청 시점에 ``set_reranker_provider("llm")`` contextvar 로 동적 전환
# ---------------------------------------------------------------------------

_RERANKER_PROVIDER: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "qa_reranker_provider", default=None
)

_VALID_PROVIDERS = frozenset({"cohere", "llm"})

# 2026-05-08: LLM rerank 시 사용할 Bedrock model ID — 사용자 모델 선택 따라가게.
# server_v2 가 body.bedrock_model_id (또는 body.reranker_llm_model) 로부터 set.
# 미설정 시 ``QA_RERANKER_LLM_MODEL`` env → 하드코딩 Haiku 4.5 폴백.
_RERANKER_LLM_MODEL: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "qa_reranker_llm_model", default=None
)


def set_reranker_llm_model(value: str | None) -> contextvars.Token:
    """LLM rerank 모델 ID contextvar set. None / 빈 문자열은 무시."""
    val = (value or "").strip()
    return _RERANKER_LLM_MODEL.set(val if val else None)


def reset_reranker_llm_model(token: contextvars.Token) -> None:
    _RERANKER_LLM_MODEL.reset(token)


def get_reranker_llm_model() -> str:
    """현재 LLM rerank 모델 ID — contextvar 우선, env, 하드코딩 순."""
    v = _RERANKER_LLM_MODEL.get()
    if v:
        return v
    return os.getenv(
        "QA_RERANKER_LLM_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    )


def _provider_env_default() -> str:
    # 기본값 LLM (Haiku 4.5) — 사용자 정책 2026-05-08:
    # task fit (자연어로 "비슷한 평가 패턴 사례 찾기") 우선.
    # Cohere (Q&A 학습) 으로 전환은 ``QA_RERANKER_PROVIDER=cohere`` env 또는
    # 프론트 토글 ``reranker_provider="cohere"`` 로 명시 시.
    val = (os.getenv("QA_RERANKER_PROVIDER", "llm") or "llm").strip().lower()
    return val if val in _VALID_PROVIDERS else "llm"


def get_reranker_provider() -> str:
    """현재 reranker provider — contextvar 우선, 없으면 환경변수."""
    v = _RERANKER_PROVIDER.get()
    if v is None:
        return _provider_env_default()
    if v not in _VALID_PROVIDERS:
        return "cohere"
    return v


def set_reranker_provider(value: str) -> contextvars.Token:
    """reranker provider contextvar set. ``"cohere"`` / ``"llm"`` 만 허용."""
    val = (value or "cohere").strip().lower()
    if val not in _VALID_PROVIDERS:
        val = "cohere"
    return _RERANKER_PROVIDER.set(val)


def reset_reranker_provider(token: contextvars.Token) -> None:
    _RERANKER_PROVIDER.reset(token)


# ---------------------------------------------------------------------------
# Runtime stats (per-request, contextvar) — 실제 호출 성공 여부 추적
#
# 프론트가 "토글 ON" 만 보고 활성 표시하면 실제 호출 실패 (권한/리전/네트워크) 를 놓침.
# rerank() 호출마다 _record_call() 로 stats 업데이트 → server_v2 가 응답 _meta 에 포함 →
# 프론트가 calls/success/fail 비교해서 신호등 (초록/노랑/빨강) 표시.
# ---------------------------------------------------------------------------

_RERANKER_STATS: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "qa_reranker_stats", default=None
)


def init_reranker_stats() -> contextvars.Token:
    """요청 시작 시 호출 — 빈 stats dict set, 종료 시 reset 토큰 반환."""
    return _RERANKER_STATS.set(
        {
            "calls": 0,
            "success": 0,
            "fail": 0,
            "last_error": None,
            "first_success_at": None,
            "last_success_at": None,
            "documents_reranked": 0,
            # 2026-05-08 — provider 별 분리 트래킹 (cohere / llm).
            "by_provider": {
                "cohere": {"calls": 0, "success": 0, "fail": 0},
                "llm": {"calls": 0, "success": 0, "fail": 0},
            },
            "last_provider": None,
        }
    )


def reset_reranker_stats(token: contextvars.Token) -> None:
    _RERANKER_STATS.reset(token)


def get_reranker_stats() -> dict[str, Any] | None:
    """현재 contextvar stats — server_v2 가 응답 빌드 시 호출."""
    s = _RERANKER_STATS.get()
    if s is None:
        return None
    # actually_active 파생값 추가 — 1회 이상 성공.
    actually_active = s.get("success", 0) > 0
    return {**s, "actually_active": actually_active}


def _record_call(
    *,
    success: bool,
    doc_count: int = 0,
    error: str | None = None,
    provider: str | None = None,
) -> None:
    s = _RERANKER_STATS.get()
    if s is None:
        return  # init 안 된 컨텍스트면 silent (테스트/배치 호출).
    s["calls"] = int(s.get("calls", 0)) + 1
    now = datetime.now(timezone.utc).isoformat()
    # provider 별 카운터 — UI 신호등 표시용.
    by_provider = s.setdefault("by_provider", {
        "cohere": {"calls": 0, "success": 0, "fail": 0},
        "llm": {"calls": 0, "success": 0, "fail": 0},
    })
    pkey = provider if provider in ("cohere", "llm") else "cohere"
    s["last_provider"] = pkey
    bucket = by_provider.setdefault(pkey, {"calls": 0, "success": 0, "fail": 0})
    bucket["calls"] = int(bucket.get("calls", 0)) + 1
    if success:
        s["success"] = int(s.get("success", 0)) + 1
        s["documents_reranked"] = int(s.get("documents_reranked", 0)) + int(doc_count)
        if not s.get("first_success_at"):
            s["first_success_at"] = now
        s["last_success_at"] = now
        bucket["success"] = int(bucket.get("success", 0)) + 1
    else:
        s["fail"] = int(s.get("fail", 0)) + 1
        if error:
            s["last_error"] = str(error)[:200]
        bucket["fail"] = int(bucket.get("fail", 0)) + 1


# ---------------------------------------------------------------------------
# Bedrock Rerank client
# ---------------------------------------------------------------------------

# Bedrock Rerank 모델 ID. ★ 2026-05-08: 정확한 ID 는 "cohere.rerank-v3-5:0" (v1 없음).
# 이전 "cohere.rerank-v3-5-v1:0" 은 ValidationException 유발 — Bedrock 측에서 reject.
_MODEL_ID = os.getenv("QA_RERANKER_MODEL", "cohere.rerank-v3-5:0")

# Bedrock Rerank 가 지원되는 리전. AWS_REGION 이 미지원 리전이면 us-west-2 로 폴백.
_SUPPORTED_REGIONS = frozenset(
    {"us-east-1", "us-west-2", "ca-central-1", "eu-central-1", "ap-northeast-1"}
)


def _resolve_region() -> str:
    explicit = os.getenv("QA_RERANKER_REGION")
    if explicit:
        return explicit.strip()
    aws_region = (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip()
    if aws_region in _SUPPORTED_REGIONS:
        return aws_region
    logger.info(
        "[reranker] AWS_REGION=%r 가 Rerank API 미지원 — us-west-2 로 폴백",
        aws_region or "(unset)",
    )
    return "us-west-2"


_client_cache: dict[str, Any] = {}


def _get_client() -> Any | None:
    """boto3 bedrock-agent-runtime 클라이언트 lazy init. 실패 시 None."""
    region = _resolve_region()
    if region in _client_cache:
        return _client_cache[region]
    try:
        import boto3  # noqa: WPS433

        client = boto3.client("bedrock-agent-runtime", region_name=region)
        _client_cache[region] = client
        logger.info("[reranker] Bedrock client init · region=%s · model=%s", region, _MODEL_ID)
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reranker] Bedrock client init 실패: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# Cohere Rerank 3.5: query 1개 = 최대 100 docs, doc 토큰 ≤ 512 (query+doc 합산).
_MAX_DOCS_PER_QUERY = 100
_MAX_DOC_CHARS = 4000  # ≈ 512 token (한국어 1 token ≈ 1.5~2자 → 안전 마진)


def rerank(
    query: str, documents: list[str], *, top_n: int
) -> tuple[list[tuple[int, float]], bool]:
    """Reranker dispatcher → ([(original_index, score), ...] desc, succeeded).

    Provider 는 ``get_reranker_provider()`` 로 결정 (cohere / llm).
    activation 체크는 호출 측 ``is_reranker_enabled()`` — 본 함수는 호출되면 무조건 시도.
    실패 / 빈 입력 시 입력 순서대로 top_n 반환 (graceful degradation, raise 안 함).

    반환:
        (order, succeeded) — succeeded=True 일 때만 점수 / reranked 마킹 사용 가능.
        succeeded=False 면 order 는 입력 순서대로의 (idx, 0.0) 폴백이며 호출 측은
        UI 가 "🎯 0.00" 을 잘못 표시하지 않도록 reranked 마킹을 건너뛰어야 함.
    """
    if not documents:
        return [], False
    n = min(top_n, len(documents))
    if n <= 0:
        return [], False

    provider = get_reranker_provider()
    if provider == "llm":
        return _rerank_via_llm(query, documents, top_n=n)
    return _rerank_via_cohere(query, documents, top_n=n)


def _rerank_via_cohere(
    query: str, documents: list[str], *, top_n: int
) -> tuple[list[tuple[int, float]], bool]:
    """Cohere Rerank 3.5 (Bedrock) — 빠른 cross-encoder."""
    n = top_n
    # 입력 정화 — None 제외, 길이 cap.
    cleaned: list[str] = []
    cleaned_idx: list[int] = []
    for i, d in enumerate(documents):
        if not d or not str(d).strip():
            continue
        cleaned.append(str(d)[:_MAX_DOC_CHARS])
        cleaned_idx.append(i)
    if not cleaned:
        return [], False

    # 100 doc 초과 시 자체 분할 과금 회피 — 첫 100 만 보내고 나머지는 무시.
    if len(cleaned) > _MAX_DOCS_PER_QUERY:
        logger.info(
            "[reranker] 입력 doc %d → %d 로 트리밍 (Cohere 100 docs/query 한도)",
            len(cleaned), _MAX_DOCS_PER_QUERY,
        )
        cleaned = cleaned[:_MAX_DOCS_PER_QUERY]
        cleaned_idx = cleaned_idx[:_MAX_DOCS_PER_QUERY]

    client = _get_client()
    if client is None:
        _record_call(success=False, error="bedrock_client_init_failed", provider="cohere")
        return [(cleaned_idx[i], 0.0) for i in range(min(n, len(cleaned)))], False

    region = _resolve_region()
    model_arn = f"arn:aws:bedrock:{region}::foundation-model/{_MODEL_ID}"

    try:
        resp = client.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": (query or "")[:_MAX_DOC_CHARS]}}],
            sources=[
                {
                    "type": "INLINE",
                    "inlineDocumentSource": {
                        "type": "TEXT",
                        "textDocument": {"text": doc},
                    },
                }
                for doc in cleaned
            ],
            rerankingConfiguration={
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "numberOfResults": n,
                    "modelConfiguration": {"modelArn": model_arn},
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("[reranker] Cohere rerank 실패 — 입력 순서 폴백: %s", err)
        _record_call(success=False, error=err, provider="cohere")
        return [(cleaned_idx[i], 0.0) for i in range(min(n, len(cleaned)))], False

    results = resp.get("results") or []
    out: list[tuple[int, float]] = []
    for r in results[:n]:
        rerank_idx = r.get("index")
        score = r.get("relevanceScore")
        if rerank_idx is None or rerank_idx >= len(cleaned_idx):
            continue
        original_idx = cleaned_idx[int(rerank_idx)]
        out.append((original_idx, float(score) if score is not None else 0.0))
    if not out:
        _record_call(success=False, error="empty_results", provider="cohere")
        return [(cleaned_idx[i], 0.0) for i in range(min(n, len(cleaned)))], False

    _record_call(success=True, doc_count=len(cleaned), provider="cohere")
    return out, True


# ---------------------------------------------------------------------------
# LLM-based Reranker (Haiku 4.5) — task-fit 강화
#
# Cohere 와 달리 자연어 프롬프트로 task 정의:
#   "transcript 와 같은 평가 패턴 / 감점 사유 가진 사례 골라줘"
#
# 모델: Haiku 4.5 (us.anthropic.claude-haiku-4-5-20251001-v1:0).
#   ``QA_RERANKER_LLM_MODEL`` env 로 override 가능 (e.g. Sonnet 으로 정확도 ↑).
#
# 비용 (Haiku 4.5 기준): ~$0.005 / call (입력 ≈3000 토큰 + 출력 ≈100 토큰)
#   Cohere ($0.002) 의 약 2.5배. Sonnet 도입 시 6.5배.
#
# Latency: ~600ms~1s (Cohere ~200ms 의 3-5x).
# ---------------------------------------------------------------------------

# LLM rerank 의 max_tokens / temperature — 모델 ID 는 ``get_reranker_llm_model()`` 동적 조회.
_LLM_RERANKER_MAX_TOKENS = 512  # 충분 — 출력은 JSON 배열만
_LLM_RERANKER_TEMPERATURE = 0.0  # deterministic


def _get_bedrock_runtime_client() -> Any | None:
    """Bedrock InvokeModel 용 runtime client (LLM rerank). 별도 캐시."""
    cache_key = "__bedrock_runtime__"
    region = _resolve_region()
    if cache_key in _client_cache:
        return _client_cache[cache_key]
    try:
        import boto3  # noqa: WPS433
        client = boto3.client("bedrock-runtime", region_name=region)
        _client_cache[cache_key] = client
        logger.info(
            "[reranker:llm] Bedrock runtime client init · region=%s · model=%s (dynamic)",
            region, get_reranker_llm_model(),
        )
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning("[reranker:llm] Bedrock runtime client init 실패: %s", exc)
        return None


def _build_llm_rerank_prompt(query: str, documents: list[str], *, top_n: int) -> str:
    """LLM 한테 던질 rerank 프롬프트 — JSON 출력 강제."""
    docs_block = "\n\n".join(
        f"[{i}] {doc[:_MAX_DOC_CHARS]}" for i, doc in enumerate(documents)
    )
    return (
        "당신은 QA 평가용 few-shot 예시 selector 입니다. "
        "주어진 transcript 와 가장 평가 패턴이 비슷한 사례를 후보에서 선별합니다.\n\n"
        "**선별 기준** (중요도 순):\n"
        "1. 동일한 감점/만점 사유가 적용 가능한 사례 (평가 패턴 일치)\n"
        "2. 비슷한 상담 상황 / 발화 스타일\n"
        "3. 표면 단어 유사도는 부차적\n\n"
        "**출력 형식**: 반드시 JSON 배열만. 설명 / markdown / 추가 텍스트 금지.\n"
        f"형식 예: [{{\"index\": 3, \"score\": 0.85}}, {{\"index\": 7, \"score\": 0.72}}]\n"
        f"score 는 0~1 범위, transcript 평가에 도움 되는 정도. 정확히 {top_n} 개 선택.\n\n"
        f"## Transcript\n{(query or '')[:_MAX_DOC_CHARS]}\n\n"
        f"## 후보 사례 ({len(documents)} 건)\n{docs_block}\n\n"
        f"위 후보 중 transcript 평가에 가장 유용한 {top_n} 개를 골라 JSON 배열로만 응답."
    )


def _rerank_via_llm(
    query: str, documents: list[str], *, top_n: int
) -> tuple[list[tuple[int, float]], bool]:
    """Haiku 4.5 (Bedrock InvokeModel) — 자연어 task 정의 reranker."""
    import json as _json
    n = top_n

    # 입력 정화
    cleaned: list[str] = []
    cleaned_idx: list[int] = []
    for i, d in enumerate(documents):
        if not d or not str(d).strip():
            continue
        cleaned.append(str(d)[:_MAX_DOC_CHARS])
        cleaned_idx.append(i)
    if not cleaned:
        return [], False

    # LLM context 부담 — 30 candidate 까지가 안전 (≈10K input token).
    if len(cleaned) > 30:
        logger.info(
            "[reranker:llm] 입력 doc %d → 30 으로 트리밍 (LLM context 절약)",
            len(cleaned),
        )
        cleaned = cleaned[:30]
        cleaned_idx = cleaned_idx[:30]

    client = _get_bedrock_runtime_client()
    if client is None:
        _record_call(success=False, error="bedrock_runtime_init_failed", provider="llm")
        return [(cleaned_idx[i], 0.0) for i in range(min(n, len(cleaned)))], False

    prompt = _build_llm_rerank_prompt(query or "", cleaned, top_n=n)
    # 사용자 모델 선택을 동적으로 따라감 — server_v2 가 set_reranker_llm_model() 로 주입.
    model_id = get_reranker_llm_model()

    try:
        resp = client.invoke_model(
            modelId=model_id,
            body=_json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": _LLM_RERANKER_MAX_TOKENS,
                "temperature": _LLM_RERANKER_TEMPERATURE,
                "messages": [{"role": "user", "content": prompt}],
            }),
            contentType="application/json",
            accept="application/json",
        )
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("[reranker:llm] InvokeModel 실패 (model=%s) — 입력 순서 폴백: %s", model_id, err)
        _record_call(success=False, error=err, provider="llm")
        return [(cleaned_idx[i], 0.0) for i in range(min(n, len(cleaned)))], False

    # 응답 파싱: Bedrock messages API → body.content[0].text
    try:
        body = _json.loads(resp["body"].read())
        text_blocks = body.get("content") or []
        text = next(
            (b.get("text", "") for b in text_blocks if b.get("type") == "text"),
            "",
        )
        # JSON 배열 추출 — LLM 이 가끔 markdown / 설명 추가하면 정규식으로 대응.
        import re as _re
        m = _re.search(r"\[\s*\{.*?\}\s*\]", text, flags=_re.DOTALL)
        json_text = m.group(0) if m else text.strip()
        parsed = _json.loads(json_text)
        if not isinstance(parsed, list):
            raise ValueError(f"LLM rerank 응답이 list 가 아님: {type(parsed).__name__}")
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("[reranker:llm] 응답 파싱 실패 — 폴백: %s", err)
        _record_call(success=False, error=err, provider="llm")
        return [(cleaned_idx[i], 0.0) for i in range(min(n, len(cleaned)))], False

    out: list[tuple[int, float]] = []
    for item in parsed[:n]:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index", -1))
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(cleaned_idx):
            continue
        # score clamp 0~1 — LLM 이 가끔 1보다 큰 값 출력
        score = max(0.0, min(1.0, score))
        original_idx = cleaned_idx[idx]
        out.append((original_idx, score))

    if not out:
        _record_call(success=False, error="empty_results", provider="llm")
        return [(cleaned_idx[i], 0.0) for i in range(min(n, len(cleaned)))], False

    _record_call(success=True, doc_count=len(cleaned), provider="llm")
    return out, True


def get_reranker_meta() -> dict[str, Any]:
    """현재 reranker 설정 메타 — 응답에 포함하여 프론트가 활성 상태 표시."""
    provider = get_reranker_provider()
    return {
        "enabled": is_reranker_enabled(),
        "provider": provider,
        "model": get_reranker_llm_model() if provider == "llm" else _MODEL_ID,
        "region": _resolve_region(),
    }


__all__ = [
    "is_reranker_enabled",
    "set_reranker_enabled",
    "reset_reranker_enabled",
    "get_reranker_provider",
    "set_reranker_provider",
    "reset_reranker_provider",
    "set_reranker_llm_model",
    "reset_reranker_llm_model",
    "get_reranker_llm_model",
    "rerank",
    "get_reranker_meta",
    "init_reranker_stats",
    "reset_reranker_stats",
    "get_reranker_stats",
]
