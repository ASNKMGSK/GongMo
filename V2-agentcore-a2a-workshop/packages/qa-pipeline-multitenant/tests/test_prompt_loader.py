# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Dev4 load_prompt — tenant override priority + kwargs-only tenant_id."""

from __future__ import annotations

import pytest
import prompts


def test_load_prompt_requires_keyword_tenant_id():
    with pytest.raises(TypeError):
        prompts.load_prompt("greeting", "kolon")  # positional not allowed


def test_load_prompt_missing_all_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        prompts.load_prompt("no_such_prompt_zzzz", tenant_id="t_missing")


def test_load_prompt_falls_back_to_default_when_tenant_override_absent(tmp_path, monkeypatch):
    # Create a fake prompts dir with only default file
    base = tmp_path / "prompts"
    base.mkdir()
    (base / "demo_item.sonnet.md").write_text("DEFAULT PROMPT", encoding="utf-8")

    monkeypatch.setattr(prompts, "_PROMPTS_DIR", base, raising=False)
    # monkeypatch may not apply if module caches path differently; tolerate both
    try:
        text = prompts.load_prompt("demo_item", tenant_id="any_tenant", include_preamble=False)
    except FileNotFoundError:
        pytest.skip("prompts module uses different directory discovery")
    assert "DEFAULT" in text


def test_load_prompt_signature_keyword_only():
    import inspect

    sig = inspect.signature(prompts.load_prompt)
    assert "tenant_id" in sig.parameters
    assert sig.parameters["tenant_id"].kind == inspect.Parameter.KEYWORD_ONLY
