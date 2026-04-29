# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Embedding backend — Bedrock Titan Embed Text v2 + in-memory cosine 검색.

설계:
- 환경변수 `QA_RAG_EMBEDDING` 으로 토글:
    * "titan" (기본 권장)  — Bedrock `amazon.titan-embed-text-v2:0` (1024-dim · L2 정규화)
    * "jaccard"            — 레거시 토큰 Jaccard (백엔드 없음)
- Titan 호출 실패 시 자동 Jaccard 로 graceful degrade.
- Embedding 캐시는 프로세스 메모리 LRU (기본 4096 엔트리). 재시작 시 초기화.
- 벡터 저장소는 외부 없이 각 RAG 모듈이 pool 을 로드할 때 임베딩을 동봉.
  (pool 크기 tenant 당 100~200 벡터 수준 → brute-force cosine 으로 충분)

Prod 연계:
- OpenSearch Serverless AOSS KNN 으로 전환 시 본 모듈의 `embed(text)` 는 그대로 사용,
  저장/검색만 AOSS `knn_vector` 쿼리로 교체. 인덱스 필드 `embedding` = 본 모듈 결과.
"""

from __future__ import annotations

import functools
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_TITAN_MODEL_ID = "amazon.titan-embed-text-v2:0"
_TITAN_DIM = 1024
_ENV_BACKEND = "QA_RAG_EMBEDDING"

_BACKEND_CACHED: Optional[str] = None
_BEDROCK_CLIENT = None


def get_backend() -> str:
    """현재 활성 임베딩 백엔드 — `titan` / `jaccard`."""
    global _BACKEND_CACHED
    if _BACKEND_CACHED is not None:
        return _BACKEND_CACHED
    raw = (os.environ.get(_ENV_BACKEND) or "titan").strip().lower()
    if raw not in ("titan", "jaccard"):
        logger.warning("%s=%r 은 미지원 값 — 'titan' 으로 대체", _ENV_BACKEND, raw)
        raw = "titan"
    _BACKEND_CACHED = raw
    return raw


def _get_bedrock():
    global _BEDROCK_CLIENT
    if _BEDROCK_CLIENT is not None:
        return _BEDROCK_CLIENT
    try:
        import boto3  # type: ignore
    except ImportError as e:
        raise RuntimeError("boto3 미설치 — pip install boto3") from e
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    _BEDROCK_CLIENT = boto3.client("bedrock-runtime", region_name=region)
    return _BEDROCK_CLIENT


@functools.lru_cache(maxsize=4096)
def embed(text: str) -> Optional[tuple[float, ...]]:
    """텍스트 → 1024-dim L2-정규화 벡터. Titan 실패 시 None.

    tuple 반환 이유: `lru_cache` key 로 쓰려면 hashable 필요.
    호출부에서는 list 로 변환해 사용.
    """
    if not text or not text.strip():
        return None
    if get_backend() != "titan":
        return None
    try:
        client = _get_bedrock()
        resp = client.invoke_model(
            modelId=_TITAN_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "inputText": text[:8000],
                "dimensions": _TITAN_DIM,
                "normalize": True,
            }),
        )
        payload = json.loads(resp["body"].read())
        vec = payload.get("embedding")
        if not isinstance(vec, list) or len(vec) != _TITAN_DIM:
            logger.warning("Titan embed: 예상치 못한 응답 차원 len=%s", len(vec) if isinstance(vec, list) else None)
            return None
        return tuple(vec)
    except Exception as e:  # noqa: BLE001  — Titan 실패 시 jaccard 로 graceful degrade
        logger.warning("Titan embed 실패 (자동 Jaccard 폴백): %s", e)
        return None


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """L2-정규화된 벡터 쌍의 코사인 유사도 — dot-product 와 동일."""
    if not a or not b or len(a) != len(b):
        return 0.0
    # Titan 은 normalize=true 이므로 |a|=|b|=1 → dot product == cosine
    return sum(x * y for x, y in zip(a, b))


def similarity(query_text: str, target_text: str, *, fallback_fn=None) -> float:
    """고수준 유사도 — Titan 사용 가능 시 cosine, 아니면 `fallback_fn` 호출.

    `fallback_fn` 시그니처: `fn(query_text, target_text) -> float`. None 이면 0.0 반환.
    """
    if get_backend() == "titan":
        qa = embed(query_text)
        tb = embed(target_text)
        if qa is not None and tb is not None:
            return cosine(qa, tb)
    if fallback_fn is not None:
        try:
            return float(fallback_fn(query_text, target_text))
        except Exception:  # noqa: BLE001
            return 0.0
    return 0.0
