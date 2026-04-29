# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tenant domain — config, store, presets.

ARCHITECTURE.md 2절 (TenantConfig), 3절 (DynamoDB) 참조.
"""

from __future__ import annotations

from .config import Industry, TenantConfig
from .store import get_config, list_configs, put_config, invalidate_cache

__all__ = [
    "TenantConfig",
    "Industry",
    "get_config",
    "put_config",
    "list_configs",
    "invalidate_cache",
]
