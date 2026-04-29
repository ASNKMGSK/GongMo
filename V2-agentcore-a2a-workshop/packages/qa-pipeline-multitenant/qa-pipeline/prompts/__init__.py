# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tenant-aware prompt loader.

ARCHITECTURE.md 7절.
호출처는 반드시 ``tenant_id`` 를 명시 — 기본값 없음.

우선순위
1. ``prompts/tenants/{tenant_id}/{item_key}.sonnet.md``
2. ``prompts/{item_key}.sonnet.md``
두 곳 모두 없으면 ``FileNotFoundError``.

``item_key`` 가 "_" 로 시작하지 않으면 ``_common_preamble.sonnet.md`` 를 자동 prepend.
preamble 자체도 테넌트 오버라이드가 있으면 그쪽을 사용.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent
_TENANTS_DIR = _PROMPTS_DIR / "tenants"
_PREAMBLE_KEY = "_common_preamble"


def _strip_front_matter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5 :]
    return text


def _resolve_path(name: str, tenant_id: str) -> Path:
    """우선순위에 따라 프롬프트 파일 경로를 해석.

    각 단계에서 ``.sonnet.md`` 를 먼저 시도하고 없으면 ``.md`` 폴백.
    """
    if not name:
        raise ValueError("name is required")
    if not tenant_id:
        raise ValueError("tenant_id is required")

    candidates: list[Path] = [
        _TENANTS_DIR / tenant_id / f"{name}.sonnet.md",
        _TENANTS_DIR / tenant_id / f"{name}.md",
        _PROMPTS_DIR / f"{name}.sonnet.md",
        _PROMPTS_DIR / f"{name}.md",
    ]
    for p in candidates:
        if p.exists():
            return p

    raise FileNotFoundError(
        f"Prompt not found: name={name!r} tenant_id={tenant_id!r} "
        f"(searched {', '.join(str(p) for p in candidates)})"
    )


@lru_cache(maxsize=512)
def _read_prompt_body(name: str, tenant_id: str) -> str:
    path = _resolve_path(name, tenant_id)
    text = path.read_text(encoding="utf-8")
    return _strip_front_matter(text).strip()


def load_prompt(
    name: str,
    *,
    tenant_id: str,
    include_preamble: bool = True,
    backend: str | None = None,
) -> str:
    """Load a prompt with tenant override + optional common preamble.

    Args:
        name: prompt key, e.g. ``"item_04_empathy"``, ``"task_planner"``,
            ``"report_generator"``, ``"consistency_check"``. Leading ``"_"``
            implicitly bypasses preamble prepending.
        tenant_id: tenant identifier. Required — no default (ARCHITECTURE.md 7절).
        include_preamble: prepend ``_common_preamble`` when True (default).
            Prompts that manage their own output rules (``consistency_check``,
            ``report_generator``) should pass ``False``.
        backend: reserved for future backend-specific branching. Currently unused —
            ``.sonnet.md`` is preferred with ``.md`` fallback regardless of backend.

    Raises:
        FileNotFoundError: neither tenant override nor default exists.
        ValueError: missing args.
    """
    del backend  # intentionally unused — kept for caller compatibility

    body = _read_prompt_body(name, tenant_id)

    if not include_preamble or name.startswith("_"):
        return body

    preamble = _read_prompt_body(_PREAMBLE_KEY, tenant_id)
    return f"{preamble}\n\n{body}"


def clear_cache() -> None:
    """Invalidate the prompt LRU cache (useful when prompt files change at runtime)."""
    _read_prompt_body.cache_clear()


__all__ = ["load_prompt", "clear_cache"]
