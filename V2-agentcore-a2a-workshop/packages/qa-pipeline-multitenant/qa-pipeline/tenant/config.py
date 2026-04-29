# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""TenantConfig dataclass — ARCHITECTURE.md 2절 스펙."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Industry = Literal[
    "industrial",
    "insurance",
    "ecommerce",
    "banking",
    "healthcare",
    "telco",
    "generic",
]

_VALID_INDUSTRIES: tuple[str, ...] = (
    "industrial",
    "insurance",
    "ecommerce",
    "banking",
    "healthcare",
    "telco",
    "generic",
)

_TENANT_ID_RE = re.compile(r"^[a-z0-9_]{2,64}$")
_QA_ITEM_MIN = 1
_QA_ITEM_MAX = 21


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class TenantConfig:
    """테넌트 설정 단일 레코드.

    저장 위치: DynamoDB 테이블 ``qa_tenants`` (PK=``tenant_id``).
    캐시: ``tenant.store.get_config(tid)`` 메모리 LRU 5분 TTL.
    """

    tenant_id: str
    display_name: str
    industry: Industry
    qa_items_enabled: list[int] = field(default_factory=list)
    score_overrides: dict[int, int] = field(default_factory=dict)
    default_models: dict[str, str] = field(default_factory=dict)
    prompt_overrides_dir: str | None = None
    branding: dict = field(default_factory=dict)
    rate_limit_per_minute: int = 60
    storage_quota_gb: int = 10
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)
    is_active: bool = True

    # ---- serialization ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """DynamoDB/JSON 직렬화용 dict 반환. dict 키는 항상 문자열로 정규화."""
        data = asdict(self)
        # DynamoDB 는 dict 키를 문자열로 요구 — int 키를 문자열로 변환
        data["score_overrides"] = {str(k): int(v) for k, v in self.score_overrides.items()}
        data["qa_items_enabled"] = list(self.qa_items_enabled)
        return data

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TenantConfig:
        """직렬화된 dict → TenantConfig. score_overrides 의 키를 int 로 복원."""
        if not isinstance(d, dict):
            raise TypeError(f"from_dict expects dict, got {type(d).__name__}")

        raw_score = d.get("score_overrides") or {}
        score_overrides: dict[int, int] = {}
        for k, v in raw_score.items():
            try:
                score_overrides[int(k)] = int(v)
            except (TypeError, ValueError) as e:
                raise ValueError(f"score_overrides key/value must be int-castable: {k}={v}") from e

        raw_items = d.get("qa_items_enabled") or []
        qa_items_enabled = [int(x) for x in raw_items]

        return cls(
            tenant_id=d["tenant_id"],
            display_name=d["display_name"],
            industry=d["industry"],
            qa_items_enabled=qa_items_enabled,
            score_overrides=score_overrides,
            default_models=dict(d.get("default_models") or {}),
            prompt_overrides_dir=d.get("prompt_overrides_dir"),
            branding=dict(d.get("branding") or {}),
            rate_limit_per_minute=int(d.get("rate_limit_per_minute", 60)),
            storage_quota_gb=int(d.get("storage_quota_gb", 10)),
            created_at=d.get("created_at") or _utcnow_iso(),
            updated_at=d.get("updated_at") or _utcnow_iso(),
            is_active=bool(d.get("is_active", True)),
        )

    # ---- validation -------------------------------------------------------

    def validate(self) -> None:
        """필드 형식/범위 검증. 실패 시 ``ValueError``."""
        if not _TENANT_ID_RE.match(self.tenant_id or ""):
            raise ValueError(
                f"tenant_id must match ^[a-z0-9_]{{2,64}}$, got: {self.tenant_id!r}"
            )
        if not self.display_name or len(self.display_name) > 128:
            raise ValueError("display_name must be 1~128 chars")
        if self.industry not in _VALID_INDUSTRIES:
            raise ValueError(
                f"industry must be one of {_VALID_INDUSTRIES}, got: {self.industry!r}"
            )

        for item in self.qa_items_enabled:
            if not isinstance(item, int) or not (_QA_ITEM_MIN <= item <= _QA_ITEM_MAX):
                raise ValueError(
                    f"qa_items_enabled entries must be int in [{_QA_ITEM_MIN},{_QA_ITEM_MAX}], got {item!r}"
                )
        if len(self.qa_items_enabled) != len(set(self.qa_items_enabled)):
            raise ValueError("qa_items_enabled must not contain duplicates")

        for k, v in self.score_overrides.items():
            if not isinstance(k, int) or not (_QA_ITEM_MIN <= k <= _QA_ITEM_MAX):
                raise ValueError(f"score_overrides key must be int 1~21, got {k!r}")
            if not isinstance(v, int) or not (1 <= v <= 100):
                raise ValueError(f"score_overrides value must be int 1~100, got {v!r}")

        if self.rate_limit_per_minute < 1 or self.rate_limit_per_minute > 100_000:
            raise ValueError("rate_limit_per_minute must be 1~100000")
        if self.storage_quota_gb < 1 or self.storage_quota_gb > 100_000:
            raise ValueError("storage_quota_gb must be 1~100000")

        if self.prompt_overrides_dir is not None:
            if not isinstance(self.prompt_overrides_dir, str) or not self.prompt_overrides_dir:
                raise ValueError("prompt_overrides_dir must be non-empty str or None")


__all__ = ["TenantConfig", "Industry"]
