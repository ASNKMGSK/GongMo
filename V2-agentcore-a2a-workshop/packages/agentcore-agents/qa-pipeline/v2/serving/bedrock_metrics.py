# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bedrock 토큰 사용량 in-memory 메트릭.

- `record_usage(usage)` — Bedrock after-call hook 에서 호출, dict 누적.
- `snapshot()` — 현재까지 누적된 합계 dict 반환 (input / output / cache_read / cache_write / call_count).
- `reset()` — 누적 카운터 초기화.

서버 endpoint `/v2/metrics/bedrock` (server_v2 가 노출) 가 snapshot 을 JSON 으로 반환.
평가 1건 시작 전에 reset → 평가 종료 후 snapshot 으로 정확한 토큰 측정 가능.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_counters: dict[str, int] = {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "call_count": 0,
}
_started_at: float = time.time()
_last_call_at: float | None = None
# 호출 단위 trace — 디버깅용 (마지막 N개만 유지)
_call_trace: list[dict[str, Any]] = []
_TRACE_MAX = 500


def record_usage(
    usage: Any,
    *,
    op: str = "Converse",
    elapsed: float | None = None,
    model_id: str | None = None,
) -> None:
    """Bedrock 응답의 `usage` dict 을 받아 누적. 비-dict 는 무시.
    ★ 2026-05-07: model_id 인자 추가. logging_setup 의 after-call 훅이 같이 전달.
    이로써 trace 엔트리에 모델별 호출 분포 보임 → 드롭다운 선택이 실제로 그 모델로 갔는지 검증.
    """
    if not isinstance(usage, dict):
        return
    try:
        inp = int(usage.get("inputTokens") or 0)
        out = int(usage.get("outputTokens") or 0)
        tot = int(usage.get("totalTokens") or (inp + out))
        cr = int(usage.get("cacheReadInputTokens") or 0)
        cw = int(usage.get("cacheWriteInputTokens") or 0)
    except (TypeError, ValueError):
        return
    global _last_call_at
    with _lock:
        _counters["input_tokens"] += inp
        _counters["output_tokens"] += out
        _counters["total_tokens"] += tot
        _counters["cache_read_tokens"] += cr
        _counters["cache_write_tokens"] += cw
        _counters["call_count"] += 1
        _last_call_at = time.time()
        _call_trace.append({
            "ts": _last_call_at,
            "op": op,
            "elapsed": elapsed,
            "model_id": model_id,
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read": cr,
            "cache_write": cw,
        })
        if len(_call_trace) > _TRACE_MAX:
            del _call_trace[: len(_call_trace) - _TRACE_MAX]


def model_breakdown() -> dict[str, dict[str, int]]:
    """trace 기반 모델별 호출/토큰 집계."""
    out: dict[str, dict[str, int]] = {}
    with _lock:
        for e in _call_trace:
            m = e.get("model_id") or "(unknown)"
            b = out.setdefault(m, {"calls": 0, "input": 0, "output": 0, "total": 0})
            b["calls"] += 1
            b["input"] += int(e.get("input_tokens") or 0)
            b["output"] += int(e.get("output_tokens") or 0)
            b["total"] += int((e.get("input_tokens") or 0) + (e.get("output_tokens") or 0))
    return out


def reset() -> dict[str, Any]:
    """누적 카운터 초기화. 직전 스냅샷 반환."""
    global _started_at, _last_call_at
    with _lock:
        prev = {
            **_counters,
            "started_at": _started_at,
            "last_call_at": _last_call_at,
            "duration_sec": (
                round((_last_call_at - _started_at), 2) if _last_call_at else 0
            ),
        }
        for k in _counters:
            _counters[k] = 0
        _started_at = time.time()
        _last_call_at = None
        _call_trace.clear()
        return prev


def snapshot() -> dict[str, Any]:
    """현재 누적 상태 + TPM/RPM 추정 (시작 이후 경과 시간 기준)."""
    with _lock:
        now = time.time()
        duration = max(now - _started_at, 0.001)
        # TPM/RPM 추정 — last_call_at 기준 최근 60초 내 호출 카운트가 더 정확하나 단순화.
        tpm = _counters["total_tokens"] / duration * 60.0
        rpm = _counters["call_count"] / duration * 60.0
        return {
            **_counters,
            "started_at": _started_at,
            "last_call_at": _last_call_at,
            "duration_sec": round(duration, 2),
            "estimated_tpm": round(tpm),
            "estimated_rpm": round(rpm, 1),
            "trace_size": len(_call_trace),
        }


def trace(limit: int = 50) -> list[dict[str, Any]]:
    """가장 최근 호출 trace 반환 (제한된 개수)."""
    with _lock:
        return list(_call_trace[-limit:])
