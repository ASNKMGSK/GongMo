# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Layer 1 (a) — STT 품질 검증.

설계서 p9:
    (a) STT 품질 검증 — 화자 분리 성공 여부, 타임스탬프 유무, 전사 신뢰도 점수 확인.
        품질 저하 시 해당 상담은 인간 검수로 자동 라우팅.

이 모듈은 LLM 호출 없이 순수 임계값 체크만 수행한다.
반환되는 `quality.unevaluable=True` 이면 orchestrator 가 Layer 2/3 를 short-circuit,
Layer 4 T3 라우팅으로 직결.
"""

from __future__ import annotations

import logging
from typing import Any

from v2.contracts.preprocessing import Quality, MaskingVersion


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 임계값 (tenant 별 override 가능 — Phase A2 이후 tenants/generic/quality_thresholds.md 로 이동)
# ---------------------------------------------------------------------------

# transcription_confidence 최소 — 0.6 미만이면 unevaluable
DEFAULT_MIN_TRANSCRIPTION_CONFIDENCE: float = 0.60

# duration_sec 최소 — 10초 미만이면 너무 짧아 평가 불가
DEFAULT_MIN_DURATION_SEC: float = 10.0

# diarization 실패 시 무조건 unevaluable (화자별 평가 불가능)


# ---------------------------------------------------------------------------
# 메인 함수
# ---------------------------------------------------------------------------


def quality_gate_check(
    stt_metadata: dict[str, Any] | None,
    *,
    min_transcription_confidence: float = DEFAULT_MIN_TRANSCRIPTION_CONFIDENCE,
    min_duration_sec: float = DEFAULT_MIN_DURATION_SEC,
) -> Quality:
    """STT 메타데이터를 검사해 품질 통과/실패 판정.

    Parameters
    ----------
    stt_metadata : dict | None
        입력 STT 메타데이터. None 이면 기본값으로 진행(경고 로그).
        기대 키:
          - transcription_confidence (float, 0.0~1.0)
          - speaker_diarization_success (bool)
          - duration_sec (float)
          - has_timestamps (bool)
          - masking_format (dict with 'version' key)
    min_transcription_confidence : float
        신뢰도 최소 임계값.
    min_duration_sec : float
        통화 길이 최소 임계값 (초).

    Returns
    -------
    Quality
        Preprocessing.quality 에 저장될 구조.
        unevaluable=True 일 경우 Layer 2/3 short-circuit 트리거.
    """
    meta = stt_metadata or {}

    transcription_confidence = _as_float(meta.get("transcription_confidence"), default=1.0)
    diarization_success = bool(meta.get("speaker_diarization_success", True))
    duration_sec = _as_float(meta.get("duration_sec"), default=60.0)
    has_timestamps = bool(meta.get("has_timestamps", False))

    masking_format = meta.get("masking_format") or {}
    masking_version: MaskingVersion = _normalize_masking_version(
        masking_format.get("version") if isinstance(masking_format, dict) else None,
    )

    reasons: list[str] = []

    if not diarization_success:
        reasons.append("diarization_failed")

    if transcription_confidence < min_transcription_confidence:
        reasons.append(
            f"transcription_confidence<{min_transcription_confidence:.2f}"
            f" (actual={transcription_confidence:.2f})"
        )

    if duration_sec < min_duration_sec:
        reasons.append(f"duration<{min_duration_sec:.0f}s (actual={duration_sec:.0f}s)")

    unevaluable = len(reasons) > 0
    tier_route_override = "T3" if unevaluable else None

    if stt_metadata is None:
        logger.warning(
            "quality_gate: stt_metadata 미제공 — 기본값으로 진행 (downstream tenant/시험 경로에서 주의)",
        )

    if unevaluable:
        logger.warning(
            "quality_gate: unevaluable=True reasons=%s",
            ", ".join(reasons),
        )
    else:
        logger.info(
            "quality_gate: passed — conf=%.2f diarization=%s duration=%.0fs mask=%s",
            transcription_confidence, diarization_success, duration_sec, masking_version,
        )

    return Quality(
        transcription_confidence=transcription_confidence,
        diarization_success=diarization_success,
        duration_sec=duration_sec,
        unevaluable=unevaluable,
        has_timestamps=has_timestamps,
        masking_version=masking_version,
        reasons=reasons,
        tier_route_override=tier_route_override,
    )


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _as_float(value: Any, *, default: float) -> float:
    """value 를 float 로 강제. 실패 시 default."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_masking_version(version: Any) -> MaskingVersion:
    """masking_format.version 값을 Literal 로 정규화.

    입력이 "v2_categorical" 이면 그대로, 그 외 (None/빈값/미지원값)는 v1_symbolic 기본.
    Layer 1 (c) pii_normalizer 가 실제 입력 토큰을 보고 자동 재감지해 덮어쓸 수 있다.
    """
    if isinstance(version, str) and version == "v2_categorical":
        return "v2_categorical"
    return "v1_symbolic"
