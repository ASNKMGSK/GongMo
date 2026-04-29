# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures for qa-pipeline-multitenant integration tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_QA_PIPELINE = _ROOT / "qa-pipeline"

if str(_QA_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_QA_PIPELINE))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
