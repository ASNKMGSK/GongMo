# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CloudWatch metric helpers — Dimension=TenantId.

ARCHITECTURE.md 9절:
- Namespace: ``QaMultiTenant``
- Dimension: ``TenantId``
- Standard metrics: ``EvaluationCount``, ``TokenUsage``, ``LatencyMs``, ``Failure``

CloudWatch MetricFilter (qa_observability_stack.py) 은 로그 기반 집계 경로이며,
본 모듈은 직접 PutMetricData 를 호출하는 동기 경로이다.
실패 시 예외를 흡수 — 관찰성 실패가 요청 처리에 영향을 주지 않는다.

환경 변수:
  ``METRICS_ENABLED`` (기본 "1") — 전면 비활성화 토글
  ``AWS_REGION`` / ``AWS_DEFAULT_REGION`` — 리전
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

METRIC_NAMESPACE = "QaMultiTenant"
METRICS_ENABLED_ENV = "METRICS_ENABLED"

_client_lock = threading.Lock()
_client: Any | None = None


def _region() -> str:
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def _enabled() -> bool:
    return os.environ.get(METRICS_ENABLED_ENV, "1").lower() in ("1", "true", "yes")


def _get_client():
    """boto3 cloudwatch 클라이언트 — 지연 생성 싱글톤."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                import boto3  # type: ignore[import-not-found]

                _client = boto3.client("cloudwatch", region_name=_region())
    return _client


def put_metric(
    metric_name: str,
    value: float,
    tenant_id: str,
    *,
    unit: str = "Count",
    extra_dimensions: dict[str, str] | None = None,
) -> None:
    """단일 PutMetricData 호출. 실패는 경고 로그만 남기고 조용히 흡수한다.

    Args:
        metric_name: 지표명 (예: "EvaluationCount", "TokenUsage", "LatencyMs", "Failure").
        value: 지표 값.
        tenant_id: 필수 — 없으면 기록 스킵.
        unit: CloudWatch 표준 단위 ("Count", "Milliseconds", "Bytes" 등).
        extra_dimensions: TenantId 외 추가 Dimension.
    """
    if not _enabled():
        return
    if not tenant_id:
        logger.debug("put_metric skipped — empty tenant_id for metric=%s", metric_name)
        return

    dimensions = [{"Name": "TenantId", "Value": tenant_id}]
    if extra_dimensions:
        for k, v in extra_dimensions.items():
            if v is None:
                continue
            dimensions.append({"Name": str(k), "Value": str(v)})

    try:
        client = _get_client()
        client.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Dimensions": dimensions,
                    "Value": float(value),
                    "Unit": unit,
                }
            ],
        )
    except Exception as e:
        logger.warning("put_metric failed metric=%s tenant=%s: %s", metric_name, tenant_id, e)


# ---- 자주 쓰는 shortcut ---------------------------------------------------


def put_evaluation_count(tenant_id: str, count: int = 1) -> None:
    put_metric("EvaluationCount", count, tenant_id, unit="Count")


def put_token_usage(tenant_id: str, tokens: int, *, model: str | None = None) -> None:
    put_metric(
        "TokenUsage",
        tokens,
        tenant_id,
        unit="Count",
        extra_dimensions={"Model": model} if model else None,
    )


def put_latency_ms(tenant_id: str, ms: float, *, route: str | None = None) -> None:
    put_metric(
        "LatencyMs",
        ms,
        tenant_id,
        unit="Milliseconds",
        extra_dimensions={"Route": route} if route else None,
    )


def put_failure(tenant_id: str, *, code: str | None = None) -> None:
    put_metric(
        "Failure",
        1,
        tenant_id,
        unit="Count",
        extra_dimensions={"Code": code} if code else None,
    )
