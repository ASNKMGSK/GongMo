# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Dev4 TenantConfig — validate/to_dict/from_dict + presets."""

from __future__ import annotations

import pytest
from tenant.config import TenantConfig


def _valid_kwargs():
    return dict(
        tenant_id="kolon_default",
        display_name="코오롱산업",
        industry="industrial",
        qa_items_enabled=[1, 2, 3],
        score_overrides={5: 10},
        default_models={"primary": "sonnet-4"},
        prompt_overrides_dir=None,
        branding={"primary_color": "#123456"},
    )


def test_validate_happy_path():
    cfg = TenantConfig(**_valid_kwargs())
    cfg.validate()


def test_validate_bad_tenant_id():
    with pytest.raises(ValueError, match="tenant_id"):
        TenantConfig(**{**_valid_kwargs(), "tenant_id": "BAD-ID!"}).validate()


def test_validate_bad_industry():
    with pytest.raises(ValueError, match="industry"):
        TenantConfig(**{**_valid_kwargs(), "industry": "space_travel"}).validate()


def test_validate_qa_items_out_of_range():
    with pytest.raises(ValueError, match="qa_items_enabled"):
        TenantConfig(**{**_valid_kwargs(), "qa_items_enabled": [22]}).validate()


def test_validate_qa_items_duplicates():
    with pytest.raises(ValueError, match="duplicates"):
        TenantConfig(**{**_valid_kwargs(), "qa_items_enabled": [1, 1]}).validate()


def test_validate_score_overrides_range():
    with pytest.raises(ValueError, match="score_overrides"):
        TenantConfig(**{**_valid_kwargs(), "score_overrides": {5: 200}}).validate()


def test_to_from_dict_roundtrip_preserves_int_keys():
    cfg = TenantConfig(**_valid_kwargs())
    d = cfg.to_dict()
    assert all(isinstance(k, str) for k in d["score_overrides"]), "to_dict keys must be str"
    restored = TenantConfig.from_dict(d)
    assert restored.score_overrides == cfg.score_overrides
    assert all(isinstance(k, int) for k in restored.score_overrides), "from_dict must restore int"


def test_preset_get_and_apply():
    from tenant.presets import get_preset, available_industries

    available = list(available_industries())
    assert "industrial" in available
    assert "generic" in available

    preset = get_preset("industrial")
    assert preset.industry == "industrial"


def test_preset_unknown_industry_raises():
    from tenant.presets import get_preset

    with pytest.raises(KeyError):
        get_preset("not_a_real_industry")


def test_apply_preset_produces_valid_config():
    from tenant.presets import apply_preset

    cfg = apply_preset(
        industry="industrial",
        tenant_id="t_industrial",
        display_name="산업",
    )
    cfg.validate()
    assert cfg.tenant_id == "t_industrial"
    assert cfg.industry == "industrial"
