# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Tenant routing/confidence 정책 로더 (PL Q5 지시 2026-04-20 외부화).

`tenants/<tenant_id>/tenant_config.yaml` 의 아래 두 섹션을 소비:

    routing:
      initial_t0_cap: float            # 0~1, 기본 0.30
      t1_sample_rate: float            # 0~1, 기본 0.05  (하한 / 단일값)
      t1_sample_rate_max: float        # 0~1, 기본 None  (상한 — 지정 시 [min, max] 범위)
      grade_boundary_margin: int       # 기본 GRADE_BOUNDARY_MARGIN

    confidence:
      rag_min_sample_size: int         # 기본 3
      rag_small_sample_weight: float   # 0~1, 기본 0.5
      item_weights:                    # 항목별 4-신호 가중치 (하드코드 ITEM_WEIGHTS override)
        "<item_number>":
          llm_self: float
          rule_llm_agreement: float
          rag_stdev: float
          evidence_quality: float

우선순위 (높은 → 낮은):
  1. 환경변수 override
     - ROUTING_INITIAL_T0_CAP / ROUTING_T1_SAMPLE_RATE / ROUTING_T1_SAMPLE_RATE_MAX
     - ROUTING_GRADE_BOUNDARY_MARGIN
     - CONFIDENCE_RAG_MIN_SAMPLE_SIZE / CONFIDENCE_RAG_SMALL_SAMPLE_WEIGHT
  2. tenant_config.yaml
  3. 코드 기본값 (본 모듈 상수)

PyYAML 가 없는 환경을 배려해 최소 YAML 파서를 함께 제공 — yaml 의존 실패 시 폴백.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from v2.schemas.enums import GRADE_BOUNDARY_MARGIN

logger = logging.getLogger(__name__)

# 코드 기본값 (YAML 미존재/로드 실패 시 최종 폴백)
_DEFAULTS = {
    "initial_t0_cap": 0.30,
    "t1_sample_rate": 0.05,
    "t1_sample_rate_max": None,  # None = 단일값 고정, float = [t1_sample_rate, max] 범위
    "grade_boundary_margin": GRADE_BOUNDARY_MARGIN,
    "rag_min_sample_size": 3,
    "rag_small_sample_weight": 0.5,
}


@dataclass(frozen=True)
class RoutingPolicy:
    """Tier 라우팅 파라미터.

    - t1_sample_rate: T1 스팟체크 하한 (단일값 호환)
    - t1_sample_rate_max: T1 스팟체크 상한. None 이면 단일값 (기존 동작 유지),
      float 이면 consultation 별 [min, max] 균등분포 추첨 (PDF §8.2 "5~10%").
    """

    initial_t0_cap: float
    t1_sample_rate: float
    t1_sample_rate_max: float | None
    grade_boundary_margin: int


@dataclass(frozen=True)
class ConfidencePolicy:
    """Confidence signal calibration 파라미터.

    - item_weights: tenant 별 항목 가중치 override. 비어 있으면 코드 ITEM_WEIGHTS fallback.
      키는 문자열 (YAML 호환): "1" ~ "18". 값은 {llm_self / rule_llm_agreement /
      rag_stdev / evidence_quality} 4개 float (합 1.0).
    """

    rag_min_sample_size: int
    rag_small_sample_weight: float
    item_weights: dict[str, dict[str, float]]


@dataclass(frozen=True)
class TenantPolicy:
    """tenant_config 요약.

    `item_weights` 는 `confidence.item_weights` 의 편의 alias (weights.get_weights 참조용).
    """

    tenant_id: str
    routing: RoutingPolicy
    confidence: ConfidencePolicy

    @property
    def item_weights(self) -> dict[str, dict[str, float]]:
        return self.confidence.item_weights


# ---------------------------------------------------------------------------
# YAML 로드 — PyYAML 우선, 실패 시 수동 파서
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        logger.debug("PyYAML unavailable — falling back to manual parser for %s", path)
        return _manual_yaml_parse(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("yaml 로드 실패 %s: %s — defaults 사용", path, exc)
        return {}


def _manual_yaml_parse(text: str) -> dict[str, Any]:
    """2 섹션(routing, confidence) 의 scalar 필드만 추출하는 경량 파서.

    PyYAML 부재 환경 폴백용. 중첩은 2단계만 지원 (top-level: section: key: value).
    """
    out: dict[str, dict[str, Any]] = {}
    current_section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" ") and line.endswith(":"):
            # top-level section 시작
            section = line[:-1].strip()
            out.setdefault(section, {})
            current_section = section
            continue
        if current_section and line.startswith("  ") and ":" in line:
            stripped = line.strip()
            # "  key: value" 또는 "  key:"
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.split("#", 1)[0].strip()
            if not val:
                continue
            # 숫자/불리언 추출
            parsed: Any = val
            try:
                if "." in val:
                    parsed = float(val)
                else:
                    parsed = int(val)
            except ValueError:
                if val.lower() in ("true", "false"):
                    parsed = val.lower() == "true"
                elif val.startswith('"') and val.endswith('"'):
                    parsed = val[1:-1]
            out[current_section][key] = parsed
    return out


def _tenant_dir(tenant_id: str) -> Path:
    """v2/tenants/<tenant_id>/ 경로. (레거시 — 단일 tenant_id 기반)"""
    # 이 파일: .../v2/routing/tenant_policy.py → parents[1] = v2
    return Path(__file__).resolve().parents[1] / "tenants" / tenant_id


def _tenants_root() -> Path:
    """v2/tenants/ 루트 경로."""
    return Path(__file__).resolve().parents[1] / "tenants"


def resolve_tenant_path(
    site_id: str,
    channel: str = "inbound",
    department: str = "default",
    *relative: str,
) -> Path | None:
    """3단계 멀티테넌트 fallback 체인으로 파일/디렉토리 해석 (2026-04-27 단순화).

    실무 표준 패턴 — 직하가 공통, 하위가 override (`_shared` 메타 폴더 폐기).

    Args:
        site_id:    업체 코드 (예: "kolon" / "cartgolf" / "generic")
        channel:    "inbound" | "outbound"
        department: 부서 자유 문자열 (예: "cs" / "retention" / "default")
        *relative:  tenant 루트 기준 상대 경로 조각

    탐색 순서 (첫 번째로 존재하는 경로 반환):
      1. tenants/{site}/{channel}/{department}/{relative}   (가장 구체)
      2. tenants/{site}/{channel}/{relative}                 (채널 직하 공통)
      3. tenants/{site}/{relative}                            (사이트 직하 공통)
      4. tenants/generic/{relative}                           (최종 fallback)

    모두 없으면 None.
    """
    base = _tenants_root()
    rel = Path(*relative) if relative else Path()

    candidates = [
        base / site_id / channel / department / rel,
        base / site_id / channel / rel,
        base / site_id / rel,
        base / "generic" / rel,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def resolve_tenant_file(
    site_id: str,
    channel: str,
    department: str,
    filename: str,
) -> Path | None:
    """3단계 fallback 체인으로 단일 파일 해석 (resolve_tenant_path 의 얇은 래퍼).

    편의 — 호출자가 `*relative` 가변 인자 대신 단순 파일명으로 쓸 때.
    """
    return resolve_tenant_path(site_id, channel, department, filename)


def _env_override(env_name: str, cast: type, default: Any) -> Any:
    raw = os.environ.get(env_name)
    if raw is None or not raw.strip():
        return default
    try:
        return cast(raw.strip())
    except (TypeError, ValueError) as exc:
        logger.warning("env %s=%r 파싱 실패: %s — default %r 사용", env_name, raw, exc, default)
        return default


def _env_override_optional_float(env_name: str, default: float | None) -> float | None:
    """Optional float env: 빈 문자열 / 'none' / 미지정 → default 유지."""
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    stripped = raw.strip()
    if not stripped or stripped.lower() == "none":
        return default
    try:
        return float(stripped)
    except ValueError as exc:
        logger.warning("env %s=%r 파싱 실패: %s — default %r 사용", env_name, raw, exc, default)
        return default


def _coerce_item_weights(raw: Any) -> dict[str, dict[str, float]]:
    """`confidence.item_weights` dict 를 str-key / float-value 로 정규화.

    키는 YAML loader 가 int 로 줄 수도 있어 str 캐스팅.
    값이 dict 가 아니거나 float 캐스팅 실패 시 해당 항목 drop (경고).
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        try:
            coerced = {sig: float(val) for sig, val in v.items()}
        except (TypeError, ValueError):
            logger.warning("item_weights['%s'] 값 float 캐스팅 실패 — 해당 항목 drop", k)
            continue
        out[str(k)] = coerced
    return out


# ---------------------------------------------------------------------------
# Public loader (캐시 활성)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=64)
def load_tenant_policy(
    tenant_id: str = "generic",
    channel: str = "inbound",
    department: str = "default",
) -> TenantPolicy:
    """tenant_config.yaml + env override → TenantPolicy 반환. 캐시됨.

    3단계 멀티테넌트 (2026-04-24): channel / department 추가.
    설정 파일은 `resolve_tenant_path` fallback 체인으로 탐색:
      tenants/{site}/{channel}/{department}/tenant_config.yaml
      → tenants/{site}/{channel}/_shared/tenant_config.yaml
      → tenants/{site}/_shared/tenant_config.yaml
      → tenants/{site}/tenant_config.yaml  (레거시 — 현재 kolon/cartgolf 위치)
      → tenants/generic/tenant_config.yaml

    호출 시 tenant_id 하나만 전달하면 channel="inbound", department="default" 로 작동 →
    기존 호출자(confidence/calculator.py, routing/tier_router.py) 와 호환.

    캐시 무효화: `load_tenant_policy.cache_clear()` (테스트용).
    """
    cfg_path = resolve_tenant_path(tenant_id, channel, department, "tenant_config.yaml")
    raw = _load_yaml(cfg_path) if cfg_path and cfg_path.exists() else {}
    routing_cfg = raw.get("routing") or {}
    confidence_cfg = raw.get("confidence") or {}

    # t1_sample_rate_max : YAML 에 있으면 float, 없으면 None (단일값 모드)
    t1_sample_rate_max_yaml = routing_cfg.get("t1_sample_rate_max", _DEFAULTS["t1_sample_rate_max"])
    t1_sample_rate_max = _env_override_optional_float(
        "ROUTING_T1_SAMPLE_RATE_MAX",
        float(t1_sample_rate_max_yaml) if t1_sample_rate_max_yaml is not None else None,
    )

    routing = RoutingPolicy(
        initial_t0_cap=_env_override(
            "ROUTING_INITIAL_T0_CAP", float,
            float(routing_cfg.get("initial_t0_cap", _DEFAULTS["initial_t0_cap"])),
        ),
        t1_sample_rate=_env_override(
            "ROUTING_T1_SAMPLE_RATE", float,
            float(routing_cfg.get("t1_sample_rate", _DEFAULTS["t1_sample_rate"])),
        ),
        t1_sample_rate_max=t1_sample_rate_max,
        grade_boundary_margin=_env_override(
            "ROUTING_GRADE_BOUNDARY_MARGIN", int,
            int(routing_cfg.get("grade_boundary_margin", _DEFAULTS["grade_boundary_margin"])),
        ),
    )
    confidence = ConfidencePolicy(
        rag_min_sample_size=_env_override(
            "CONFIDENCE_RAG_MIN_SAMPLE_SIZE", int,
            int(confidence_cfg.get("rag_min_sample_size", _DEFAULTS["rag_min_sample_size"])),
        ),
        rag_small_sample_weight=_env_override(
            "CONFIDENCE_RAG_SMALL_SAMPLE_WEIGHT", float,
            float(confidence_cfg.get(
                "rag_small_sample_weight", _DEFAULTS["rag_small_sample_weight"]
            )),
        ),
        item_weights=_coerce_item_weights(confidence_cfg.get("item_weights")),
    )
    policy = TenantPolicy(tenant_id=tenant_id, routing=routing, confidence=confidence)
    logger.info(
        "load_tenant_policy[%s]: t0_cap=%.2f t1_rate=%.3f t1_max=%s margin=%d "
        "rag_min_n=%d rag_weight=%.2f item_weights_entries=%d",
        tenant_id, routing.initial_t0_cap, routing.t1_sample_rate,
        routing.t1_sample_rate_max, routing.grade_boundary_margin,
        confidence.rag_min_sample_size, confidence.rag_small_sample_weight,
        len(confidence.item_weights),
    )
    return policy


def reset_cache() -> None:
    """테스트 전용 — env 변경 후 재로드."""
    load_tenant_policy.cache_clear()
