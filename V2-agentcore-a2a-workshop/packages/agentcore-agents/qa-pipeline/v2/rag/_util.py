# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""V2 RAG 공통 유틸 — 경로 해결, 토큰화, 단순 유사도."""

from __future__ import annotations

import os
import re
from functools import lru_cache


_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def tenant_dir(tenant_id: str) -> str:
    """`tenants/<tenant_id>/` 절대 경로 반환. (레거시 — 단일 tenant_id 기반)"""
    return os.path.join(_PIPELINE_DIR, "v2", "tenants", tenant_id)


def resolve_tenant_subdir(
    tenant_id: str,
    subdir: str,
    channel: str = "inbound",
    department: str = "default",
) -> str:
    """3단계 멀티테넌트 fallback 체인으로 하위 디렉토리 경로 해석 (2026-04-27 단순화).

    실무 표준 (i18n / Linux conf / Helm) 의 "직하 = 공통, 하위 = override" 패턴으로 정리.
    이전 `_shared` 메타 폴더 패턴 폐기.

    탐색 순서 (첫 번째로 존재하는 경로 반환):
      1. tenants/{tenant}/{channel}/{department}/{subdir}   (가장 구체)
      2. tenants/{tenant}/{channel}/{subdir}                 (채널 직하 = 채널 공통)
      3. tenants/{tenant}/{subdir}                            (사이트 직하 = 사이트 공통)
      4. tenants/generic/{subdir}                             (최종 fallback)

    모두 없으면 마지막 후보 (존재 안 해도) 경로 문자열 반환 → 호출자 측에서
    read_text()/존재 체크 실패로 이어져 RAGUnavailable 등으로 처리됨.
    """
    base = os.path.join(_PIPELINE_DIR, "v2", "tenants")
    candidates = [
        os.path.join(base, tenant_id, channel, department, subdir),
        os.path.join(base, tenant_id, channel, subdir),
        os.path.join(base, tenant_id, subdir),
        os.path.join(base, "generic", subdir),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[-1]  # 마지막 fallback 경로 (없어도 반환 — 호출자가 처리)


_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """한/영/숫자 토큰만 추출 (간단 토크나이저 — prototype 전용)."""
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def jaccard(a: list[str], b: list[str]) -> float:
    """Jaccard 유사도 — 0.0 ~ 1.0."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def overlap_ratio(query_tokens: list[str], target_tokens: list[str]) -> float:
    """쿼리 토큰 중 타겟에 존재하는 비율 — V1 sample_data 방식."""
    if not query_tokens:
        return 0.0
    hits = sum(1 for t in query_tokens if t in target_tokens)
    return hits / len(query_tokens)


@lru_cache(maxsize=8)
def read_text(path: str) -> str:
    """파일 전체 텍스트 읽기 (캐시)."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
