# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# =============================================================================
# LLM 유틸리티 모듈 -- SageMaker vLLM 엔드포인트 연동
# =============================================================================
# 이 모듈은 QA 파이프라인의 모든 노드가 공통으로 사용하는 LLM 호출 헬퍼이다.
# SageMaker에 배포된 vLLM 엔드포인트(OpenAI 호환 API)를 사용한다.
#
# [핵심 기능]
# 1. SageMaker vLLM 엔드포인트 호출 (OpenAI chat completion 형식)
# 2. 인스턴스 캐싱: 동일 설정의 모델은 재생성하지 않고 캐시된 인스턴스 재사용
# 3. Qwen 3 <think> 태그 자동 제거 (사고 과정 제거, 응답만 반환)
# 4. JSON 파싱: LLM 응답에서 JSON을 추출 (마크다운 코드 펜스 자동 처리)
# 5. 비동기 호출: ainvoke_llm()으로 async/await 기반 비동기 LLM 호출 지원
#
# [파이프라인 내 위치]
# 모든 LLM 기반 노드(평가 에이전트들, consistency_check, report_generator)가
# 이 모듈을 통해 LLM을 호출한다.
# =============================================================================

"""
Shared LLM invocation helper for QA pipeline nodes.

Uses SageMaker vLLM endpoint (Qwen 3 8B) with OpenAI-compatible API.
"""

from __future__ import annotations

import asyncio
import boto3
import hashlib
import json
import logging
import os
import re

# 프로젝트 루트의 중앙 설정 — 환경변수 해석은 config.py가 전담
import sys as _sys
import time
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError as _BotoClientError, ReadTimeoutError as _BotoReadTimeoutError
from langchain_aws import ChatBedrockConverse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pathlib import Path as _Path
from typing import Any


_project_root = str(_Path(__file__).resolve().parent.parent)
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)

from config import app_config  # noqa: E402
from nodes.json_parser import _extract_text, _strip_think_tags, parse_llm_json  # noqa: E402, F401


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM 백엔드 선택 — 비교 테스트용 토글
# ---------------------------------------------------------------------------
# LLM_BACKEND 환경변수로 백엔드 전환:
#   - "bedrock"  : Bedrock Sonnet 4.6 (기본값, 현재 비교 테스트 중)
#   - "sagemaker": 기존 SageMaker vLLM (Qwen3-8B)
# 테스트 후 고정하려면 기본값을 바꾸거나 env 를 설정.

LLM_BACKEND = os.environ.get("LLM_BACKEND", "bedrock").strip().lower()

# Bedrock 모델 ID (Sonnet 4.6 inference profile)
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

# ---------------------------------------------------------------------------
# SageMaker Endpoint 설정 (중앙 config에서 읽음)
# ---------------------------------------------------------------------------

SAGEMAKER_ENDPOINT = app_config.sagemaker.endpoint_name
AWS_REGION = app_config.sagemaker.region

# SageMaker vLLM container 60s 하드 timeout 회피용 출력 토큰 상한.
# decode ≈ 50ms/token → 512 token ≈ 25s. 호출자 max_tokens 가 더 커도 clamp 됨.
# 환경변수 SAGEMAKER_MAX_OUTPUT_TOKENS 로 조정 가능.
SAGEMAKER_MAX_OUTPUT_TOKENS = int(os.environ.get("SAGEMAKER_MAX_OUTPUT_TOKENS", "512"))

# 동시 요청 제한: LLM 백엔드 쓰로틀링 회피용 세마포어
# 8개 에이전트가 각 2~3회 LLM 호출 → 최대 ~18개 동시 요청 발생
# Bedrock(기본): 10 권장 (계정 RPS 한도 내 여유), SageMaker 단일 GPU: 3~4 권장.
MAX_CONCURRENT_REQUESTS = app_config.sagemaker.max_concurrent
# ★ 2026-04-27: per-event-loop 세마포어. asyncio.Semaphore 는 생성 시점의 loop 에 묶이므로,
# 모듈 전역 single 인스턴스를 쓰면 `asyncio.run(...)` 으로 새 loop 를 만든 호출자
# (예: post-debate judge) 가 acquire 시 RuntimeError("bound to a different event loop") 발생.
# loop id 를 키로 dict 캐시 → 호출자 loop 마다 별도 Semaphore 생성·재사용.
_semaphores_by_loop: dict[int, asyncio.Semaphore] = {}


def _get_semaphore() -> asyncio.Semaphore:
    """현재 event loop 에 묶인 비동기 세마포어 반환 (loop 마다 별도 인스턴스)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # running loop 없음 — fallback: 새 Semaphore (단발성, 캐시 안 함)
        return asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    key = id(loop)
    sem = _semaphores_by_loop.get(key)
    if sem is None:
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        _semaphores_by_loop[key] = sem
        logger.info(
            "LLM concurrency limiter: max %d concurrent (loop=%d, total_loops=%d)",
            MAX_CONCURRENT_REQUESTS, key, len(_semaphores_by_loop),
        )
    return sem


# ---------------------------------------------------------------------------
# LLM 타임아웃 전용 예외 — 파이프라인 중단을 위한 시그널
# ---------------------------------------------------------------------------
# 각 노드의 except Exception 블록이 이걸 "partial" 결과로 삼켜버리지 않도록,
# LLM 노드들은 이 예외가 오면 즉시 re-raise 하여 server.py 까지 전파한다.
# server.py 가 SSE error 이벤트로 변환해 프론트에 알린다.


class LLMTimeoutError(Exception):
    """LLM 응답 대기 시간 초과 — 파이프라인을 중단하고 프론트에 알림 전송."""

    pass


# boto3 SageMaker Runtime 클라이언트 (싱글턴)
_sm_client = None

# 타임아웃 임계값 (초) — 프론트 메시지에 동일 값 사용
LLM_READ_TIMEOUT_SECONDS = 240

# 서버 측 container timeout (60s 하드 리밋) 재시도 설정
# 원인: SageMaker 실시간 엔드포인트의 container invocation timeout 은 60s 로 고정 (AWS 하드 리밋).
#      vLLM 큐에 요청이 쌓여 있으면 일부 요청이 60s 안에 처리되지 못하고 timeout.
# 전략: 동일 요청 **1회만** 재시도 — 큐 부하가 transient 하면 재시도 시 성공할 확률 높음.
#      재시도도 실패하면 더 이상 시도하지 않고 LLMTimeoutError 발생.
SERVER_TIMEOUT_MAX_RETRIES = 1  # 재시도 횟수 (1회만)
SERVER_TIMEOUT_RETRY_BACKOFF = 3  # 재시도 전 대기 시간 (초) — vLLM 큐 배출 시간 확보


def _get_sm_client():
    """SageMaker Runtime 클라이언트 싱글턴.

    정책: LLM 응답 대기 240초 초과 시 **재시도 없이 즉시 실패**.
    - 실측 사례: empathy 평가에서 default(read_timeout=60, retries=5) 로 308s 대기 후 실패.
    - 정책 의도: "그냥 멈춰있는 것" 방지 → 240s 한계 초과 시 단일 실패로 종결, 프론트에 알림.
    """
    global _sm_client
    if _sm_client is None:
        sm_cfg = BotoConfig(
            read_timeout=LLM_READ_TIMEOUT_SECONDS,
            connect_timeout=10,
            retries={"max_attempts": 1, "mode": "standard"},  # 재시도 없이 1회만
        )
        _sm_client = boto3.client("sagemaker-runtime", region_name=AWS_REGION, config=sm_cfg)
        logger.info(
            "SageMaker Runtime client created: region=%s, read_timeout=%ds, max_attempts=1",
            AWS_REGION,
            LLM_READ_TIMEOUT_SECONDS,
        )
    return _sm_client


# ---------------------------------------------------------------------------
# 메시지 변환 / 응답 후처리
# ---------------------------------------------------------------------------


NO_THINK_INSTRUCTION = "/no_think\nDo not use <think> tags. Respond directly without any thinking process."

# Qwen3 특수 토큰 및 프롬프트 인젝션에 사용될 수 있는 위험 패턴
_DANGEROUS_PATTERNS: list[str] = [
    "<|im_start|>",
    "<|im_end|>",
    "<|endoftext|>",
    "system\n",
    "assistant\n",
    "[지시사항]",
    "[판정]",
]


def sanitize_input(text: str, *, max_length: int = 5000) -> str:
    """프롬프트 인젝션 방어 — 사용자/고객 발화가 LLM 메시지에 삽입될 때
    시스템 지시를 덮어쓰는 공격을 차단한다.

    sLLM(Qwen3-8B)은 대형 LLM보다 인젝션에 취약하므로 모든 메시지를
    SageMaker로 전송하기 전에 이 함수를 적용한다.

    처리 항목:
      1. Qwen3 특수 토큰 제거 (<|im_start|>, <|im_end|>, <|endoftext|>)
      2. 시스템 프롬프트 구분자 제거 (인젝션 벡터 차단)
      3. 과도한 공백 정리 (연속 공백 → 단일 공백)
      4. 최대 길이 제한 (DoS 방지)
    """
    sanitized = text
    for pattern in _DANGEROUS_PATTERNS:
        sanitized = sanitized.replace(pattern, "")
    # 과도한 공백 정리 (연속 공백 → 단일 공백, 앞뒤 공백 제거)
    sanitized = re.sub(r"[ \t]+", " ", sanitized).strip()
    # 최대 길이 제한
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "...(truncated)"
    return sanitized


def _to_openai_messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """LangChain 메시지를 OpenAI chat 형식으로 변환.

    Qwen 3 thinking 비활성화 + 모든 메시지 내용을 sanitize_input()으로 정화.
    """
    result = []
    has_system = False
    for msg in messages:
        if isinstance(msg, SystemMessage):
            result.append({"role": "system", "content": NO_THINK_INSTRUCTION + "\n\n" + sanitize_input(msg.content)})
            has_system = True
        elif isinstance(msg, HumanMessage):
            result.append({"role": "user", "content": sanitize_input(msg.content)})
        elif isinstance(msg, AIMessage):
            result.append({"role": "assistant", "content": sanitize_input(msg.content)})
        else:
            result.append({"role": "user", "content": sanitize_input(str(msg.content))})
    if not has_system:
        result.insert(0, {"role": "system", "content": NO_THINK_INSTRUCTION})
    return result


# ---------------------------------------------------------------------------
# ChatSageMakerVLLM — LangChain BaseChatModel 래퍼
# ---------------------------------------------------------------------------


class ChatSageMakerVLLM(BaseChatModel):
    """SageMaker vLLM 엔드포인트용 LangChain Chat 모델 래퍼.

    vLLM이 제공하는 OpenAI 호환 chat completion API를 사용하며,
    boto3 sagemaker-runtime invoke_endpoint로 호출한다.
    """

    endpoint_name: str = SAGEMAKER_ENDPOINT
    region_name: str = AWS_REGION
    temperature: float = 0.1
    max_tokens: int = 4096

    @property
    def _llm_type(self) -> str:
        return "sagemaker-vllm"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"endpoint_name": self.endpoint_name, "temperature": self.temperature, "max_tokens": self.max_tokens}

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> ChatResult:
        """동기 LLM 호출.

        서버 측 container timeout (60s 하드 리밋) 발생 시 1회 재시도.
        재시도는 vLLM 큐 부하로 인한 transient timeout 을 완화하기 위함.
        """
        client = _get_sm_client()

        # SageMaker vLLM container 가 60s 하드 timeout 이라 출력 토큰을 제한.
        # decode 시간 = output_tokens × ~50ms → 512 token ≈ 25s (timeout 마진 확보).
        # 호출자가 더 큰 값을 요청해도 강제로 clamp.
        effective_max_tokens = min(int(self.max_tokens), SAGEMAKER_MAX_OUTPUT_TOKENS)

        body: dict[str, Any] = {
            "messages": _to_openai_messages(messages),
            "max_tokens": effective_max_tokens,
            "temperature": self.temperature,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if stop:
            body["stop"] = stop
        body_json = json.dumps(body, ensure_ascii=False)

        # 총 시도 횟수 = 초기 1회 + 재시도 횟수 (SERVER_TIMEOUT_MAX_RETRIES)
        total_attempts = 1 + SERVER_TIMEOUT_MAX_RETRIES
        resp = None
        for attempt in range(1, total_attempts + 1):
            try:
                resp = client.invoke_endpoint(
                    EndpointName=self.endpoint_name, ContentType="application/json", Body=body_json
                )
                break  # 성공 시 즉시 종료
            except _BotoReadTimeoutError as e:
                # (1) Client-side read_timeout 초과 — 재시도하지 않음 (240s 대기 = 심각한 이상)
                logger.warning(
                    "LLM timeout (client %ds exceeded): endpoint=%s", LLM_READ_TIMEOUT_SECONDS, self.endpoint_name
                )
                raise LLMTimeoutError(
                    f"LLM 응답 대기 {LLM_READ_TIMEOUT_SECONDS}초 초과 (client timeout, endpoint={self.endpoint_name})"
                ) from e
            except _BotoClientError as e:
                # (2) Server-side timeout — SageMaker container 응답 대기 중 60s 경과.
                # HTTP 200 + ModelError 로 내려옴 (ReadTimeoutError 로는 안 잡힘).
                err_msg = str(e).lower()
                is_server_timeout = "timed out" in err_msg or "timeout" in err_msg
                if not is_server_timeout:
                    raise  # timeout 이 아닌 ClientError (auth, throttle 등) 는 전파
                if attempt < total_attempts:
                    # 재시도 — vLLM 큐가 비워지도록 짧은 backoff 후 동일 요청 재전송
                    logger.warning(
                        "LLM server timeout (attempt %d/%d): endpoint=%s — retrying in %ds",
                        attempt,
                        total_attempts,
                        self.endpoint_name,
                        SERVER_TIMEOUT_RETRY_BACKOFF,
                    )
                    import time as _time

                    _time.sleep(SERVER_TIMEOUT_RETRY_BACKOFF)
                    continue
                # 최종 실패 — 재시도까지 실패
                logger.warning(
                    "LLM timeout (server container, %d attempts exhausted): endpoint=%s, msg=%s",
                    total_attempts,
                    self.endpoint_name,
                    e,
                )
                raise LLMTimeoutError(
                    f"SageMaker 모델 응답 타임아웃 — container 가 시간 내에 응답하지 못했습니다 "
                    f"(재시도 {SERVER_TIMEOUT_MAX_RETRIES}회 후 실패, endpoint={self.endpoint_name})"
                ) from e
        assert resp is not None, "resp must be set if we broke out of retry loop"
        result = json.loads(resp["Body"].read())

        raw_content = result["choices"][0]["message"]["content"]
        content = _strip_think_tags(raw_content)

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None, **kwargs: Any) -> ChatResult:
        """비동기 LLM 호출 — 세마포어로 동시 요청 수 제한 + run_in_executor.

        aiobotocore 전환 시 네이티브 async 가능하나, boto3 의존성 이중화/안정성 리스크 대비
        run_in_executor 방식 유지. 이벤트 루프 차단 없이 동시성 확보됨.
        """
        sem = _get_semaphore()
        async with sem:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._generate, messages, stop)


# ---------------------------------------------------------------------------
# 모델 인스턴스 캐싱 (LRU, maxsize=16)
# ---------------------------------------------------------------------------

_MODEL_CACHE_MAXSIZE = 16
_cached_models: dict[tuple, BaseChatModel] = {}


def _evict_oldest_model() -> None:
    """FIFO eviction when cache exceeds maxsize."""
    while len(_cached_models) >= _MODEL_CACHE_MAXSIZE:
        oldest_key = next(iter(_cached_models))
        _cached_models.pop(oldest_key)
        logger.debug("Evicted model cache entry: %s", oldest_key)


# ---------------------------------------------------------------------------
# LLM 응답 인메모리 캐싱 (TTL 1시간)
# ---------------------------------------------------------------------------

_RESPONSE_CACHE_TTL = 3600
_RESPONSE_CACHE_MAXSIZE = 256
_response_cache: dict[str, tuple[float, Any]] = {}


def _response_cache_key(messages: list, backend: str | None, model_id: str | None, max_tokens: int) -> str:
    """Build a deterministic cache key from message content + LLM params."""
    parts = []
    for msg in messages:
        content = msg.content if hasattr(msg, "content") else str(msg)
        parts.append(content)
    raw = f"{backend}|{model_id}|{max_tokens}|{'||'.join(parts)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_cached_response(key: str) -> Any | None:
    """Return cached response if exists and not expired, else None."""
    entry = _response_cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _RESPONSE_CACHE_TTL:
        _response_cache.pop(key, None)
        return None
    return value


def _put_cached_response(key: str, value: Any) -> None:
    """Store response in cache, evicting oldest entries if over maxsize."""
    while len(_response_cache) >= _RESPONSE_CACHE_MAXSIZE:
        oldest_key = next(iter(_response_cache))
        _response_cache.pop(oldest_key)
    _response_cache[key] = (time.time(), value)


def _resolve_backend(backend: str | None) -> str:
    """요청 단위 backend (state.llm_backend) 우선, 없으면 env var 기본값."""
    if backend:
        b = backend.strip().lower()
        if b in ("bedrock", "sagemaker"):
            return b
    return LLM_BACKEND


def _resolve_bedrock_model_id(bedrock_model_id: str | None) -> str:
    """요청 단위 Bedrock 모델 ID 우선, 없으면 env var 기본값.

    유효성 검증은 최소화 — 실제 모델 호출 시 Bedrock API 가 에러 반환.
    """
    if bedrock_model_id:
        v = bedrock_model_id.strip()
        if v:
            return v
    return BEDROCK_MODEL_ID


def get_chat_model(
    *,
    temperature: float = app_config.sagemaker.default_temperature,
    max_tokens: int = app_config.sagemaker.default_max_tokens,
    backend: str | None = None,
    bedrock_model_id: str | None = None,
) -> BaseChatModel:
    """캐시된 LLM 인스턴스를 반환한다 (Bedrock 또는 SageMaker).

    Args:
        temperature: 샘플링 온도.
        max_tokens: 최대 출력 토큰.
        backend: 요청 단위 오버라이드 ("bedrock" | "sagemaker" | None).
            None 이면 LLM_BACKEND 환경변수 기본값 사용.
        bedrock_model_id: 요청 단위 Bedrock 모델 ID 오버라이드.
            None 이면 BEDROCK_MODEL_ID 환경변수 기본값 사용. sagemaker 백엔드에선 무시.
    """
    chosen = _resolve_backend(backend)
    effective_model_id = _resolve_bedrock_model_id(bedrock_model_id)
    # Bedrock Sonnet 은 Qwen3 보다 verbose — 동일 max_tokens 이면 쉽게 잘림.
    # 1.2배 여유분으로 충분. ×2 는 과다 할당 (report_generator 4096→8192 시 timeout 실측 300s+).
    if chosen == "bedrock":
        effective_max_tokens = min(int(max_tokens * 1.2), 4096)
    else:
        effective_max_tokens = max_tokens
    # 캐시 키에 effective_model_id 포함 — 같은 Bedrock 이라도 모델 ID 가 다르면
    # 별도 인스턴스. sagemaker 백엔드는 effective_model_id 가 기본값이라 키 영향 없음.
    key = (chosen, temperature, effective_max_tokens, effective_model_id)

    if key in _cached_models:
        return _cached_models[key]

    _evict_oldest_model()

    if chosen == "bedrock":
        # 2026-04-21 3-Persona 앙상블 반영: 17 항목 × 3 persona ≈ 51 동시 호출 burst.
        # max_pool_connections 20 → 50 상향 — 31+ 동시 요청을 pool wait 없이 즉시 발사.
        # iter04: max_attempts 2→4, mode adaptive (Bedrock throttle 쏠림 대응).
        # iter03_clean 배치에서 62건 ThrottlingException 발생 → reconciler 로 흡수.
        # 2026-04-21 Pool 경고 수정: LangChain `config=` 주입 시 pool_size 가 10 으로
        # 관찰되어 boto3 client 를 직접 만들어 `client=` 로 전달 — 확정적으로 50 적용.
        boto_cfg = BotoConfig(
            read_timeout=180,
            connect_timeout=10,
            retries={"max_attempts": 4, "mode": "adaptive"},
            max_pool_connections=50,
        )
        bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=AWS_REGION,
            config=boto_cfg,
        )
        instance = ChatBedrockConverse(
            model=effective_model_id,
            region_name=AWS_REGION,
            temperature=temperature,
            max_tokens=effective_max_tokens,
            client=bedrock_client,
        )
        logger.info(
            "Created Bedrock LLM: model=%s, region=%s, temp=%s, max_tokens=%s (caller asked %s)",
            effective_model_id,
            AWS_REGION,
            temperature,
            effective_max_tokens,
            max_tokens,
        )
    else:
        # --- SageMaker vLLM (Qwen3-8B) — backend="sagemaker" 로 활성화 ---
        instance = ChatSageMakerVLLM(endpoint_name=SAGEMAKER_ENDPOINT, temperature=temperature, max_tokens=max_tokens)
        logger.info(
            "Created SageMaker LLM: endpoint=%s, temp=%s, max_tokens=%s", SAGEMAKER_ENDPOINT, temperature, max_tokens
        )

    _cached_models[key] = instance
    return instance


async def ainvoke_llm(
    messages: list,
    *,
    temperature: float = app_config.sagemaker.default_temperature,
    max_tokens: int = app_config.sagemaker.default_max_tokens,
    backend: str | None = None,
    bedrock_model_id: str | None = None,
) -> str:
    """비동기 LLM 호출 — 응답 텍스트(str)를 반환한다. TTL 1시간 인메모리 캐시."""
    import asyncio as _asyncio

    cache_key = _response_cache_key(messages, backend, bedrock_model_id, max_tokens)
    cached = _get_cached_response(cache_key)
    if cached is not None:
        logger.info("🔁 LLM cache HIT key=%s (ainvoke_llm)", cache_key[:12])
        return cached
    llm = get_chat_model(
        temperature=temperature, max_tokens=max_tokens, backend=backend, bedrock_model_id=bedrock_model_id
    )
    req_id = cache_key[:8]
    try:
        total_chars = sum(
            len(str(getattr(m, "content", ""))) if not isinstance(m, dict) else len(str(m.get("content", "")))
            for m in messages
        )
    except Exception:
        total_chars = -1
    task_name = _asyncio.current_task().get_name() if _asyncio.current_task() else "-"
    logger.info(
        "⬆️ LLM REQ [%s] backend=%s model=%s msgs=%d chars=%d temp=%s max_tokens=%d task=%s",
        req_id,
        backend or "default",
        bedrock_model_id or "-",
        len(messages),
        total_chars,
        temperature,
        max_tokens,
        task_name,
    )
    t0 = _asyncio.get_event_loop().time()
    try:
        response = await llm.ainvoke(messages)
    except Exception as e:
        dt = _asyncio.get_event_loop().time() - t0
        logger.exception("💥 LLM ERR [%s] %.2fs · %s: %s", req_id, dt, type(e).__name__, str(e)[:200])
        raise
    dt = _asyncio.get_event_loop().time() - t0
    result = _extract_text(response.content)
    logger.info("⬇️ LLM RES [%s] %.2fs · %d chars", req_id, dt, len(result))
    _put_cached_response(cache_key, result)
    return result


async def invoke_and_parse(llm: BaseChatModel, messages: list) -> dict:
    """Invoke LLM and parse JSON response in one call. TTL 1시간 인메모리 캐시.

    통합 호출: await llm.ainvoke(messages) → parse_llm_json(response.content).
    노드 전반에서 반복되는 2-step 패턴을 대체한다.

    파싱 실패 시 1회 재시도 (corrective system message 추가) — Bedrock 의 stochastic
    JSON 오류 회피용. 재시도도 실패하면 원본 예외 raise.
    """
    import asyncio as _asyncio
    from langchain_core.messages import SystemMessage

    cache_key = _response_cache_key(messages, None, None, 0)
    cached = _get_cached_response(cache_key)
    if cached is not None:
        logger.info("🔁 LLM cache HIT key=%s", cache_key[:12])
        return cached

    # 요청 크기 로깅 — messages 총 길이 합산
    try:
        total_chars = sum(
            len(str(getattr(m, "content", ""))) if not isinstance(m, dict) else len(str(m.get("content", "")))
            for m in messages
        )
    except Exception:
        total_chars = -1
    req_id = cache_key[:8]
    model_id = getattr(llm, "model_id", None) or getattr(llm, "model", "?")
    task_name = _asyncio.current_task().get_name() if _asyncio.current_task() else "-"
    logger.info(
        "⬆️ LLM REQ [%s] model=%s msgs=%d total_chars=%d task=%s",
        req_id,
        model_id,
        len(messages),
        total_chars,
        task_name,
    )
    t0 = _asyncio.get_event_loop().time()

    # ★ Bedrock throttle 방지 — 모든 LLM 호출을 세마포어로 감쌈.
    # MAX_CONCURRENT_REQUESTS (SAGEMAKER_MAX_CONCURRENT env) 값으로 제한.
    # 기존엔 SageMaker 커스텀 클래스 경로에만 세마포어 적용돼서 Bedrock 호출은 무제한 동시
    # 실행 → 30+ 요청 동시 시 ThrottlingException. 여기서 전역 세마포어로 단일화.
    sem = _get_semaphore()
    async with sem:
        try:
            response = await llm.ainvoke(messages)
        except Exception as e:
            dt = _asyncio.get_event_loop().time() - t0
            logger.exception("💥 LLM ERR [%s] %.2fs · %s: %s", req_id, dt, type(e).__name__, str(e)[:200])
            raise

    dt = _asyncio.get_event_loop().time() - t0
    resp_text = response.content if isinstance(response.content, str) else str(response.content)
    usage = getattr(response, "usage_metadata", None) or getattr(response, "response_metadata", {}).get("usage", {})
    logger.info(
        "⬇️ LLM RES [%s] %.2fs · %d chars · usage=%s",
        req_id,
        dt,
        len(resp_text),
        usage if usage else "n/a",
    )
    try:
        result = parse_llm_json(response.content)
        _put_cached_response(cache_key, result)
        return result
    except Exception as first_err:
        logger.warning(
            "⚠ LLM PARSE FAIL [%s] — 1차 실패 재시도. err=%s, snippet=%r",
            req_id,
            first_err,
            resp_text[:200],
        )

    # 재시도: corrective system message 를 messages 앞에 추가
    correction = SystemMessage(content=(
        "이전 응답이 JSON 파싱 실패했습니다. 반드시 다음을 준수하세요:\n"
        "1. 응답은 **순수 JSON 객체** 만 출력. 마크다운 펜스(```) 금지.\n"
        "2. 모든 문자열 값 안의 줄바꿈은 \\n 으로 escape.\n"
        "3. 모든 문자열 값 안의 큰따옴표는 \\\" 로 escape.\n"
        "4. 모든 키/값에 큰따옴표 사용 (작은따옴표 금지).\n"
        "5. 마지막 항목 뒤 trailing comma 금지.\n"
        "JSON 만 출력하세요."
    ))
    retry_messages = [correction] + list(messages)
    logger.info("🔁 LLM RETRY [%s] corrective SystemMessage 추가", req_id)
    t1 = _asyncio.get_event_loop().time()
    retry_response = await llm.ainvoke(retry_messages)
    dt2 = _asyncio.get_event_loop().time() - t1
    retry_text = retry_response.content if isinstance(retry_response.content, str) else str(retry_response.content)
    logger.info("⬇️ LLM RES [%s·retry] %.2fs · %d chars", req_id, dt2, len(retry_text))
    result = parse_llm_json(retry_response.content)
    logger.info("✅ LLM RETRY OK [%s] 2차 재시도 파싱 성공", req_id)
    _put_cached_response(cache_key, result)
    return result
