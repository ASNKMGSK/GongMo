# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# NOTE: This script is for offline validation only. Do NOT invoke AWS APIs.
#
# 목적: `state.build_initial_state(...)` 가 생성한 QAState 의 key 집합이
# 원본 single-tenant 파이프라인 관례와 일치하는지 정적으로 검증한다.
#
# - 외부 I/O 없음 (boto3 / requests / AWS SDK import 금지)
# - Dev3 `state.py` 와 `nodes/skills/node_context.py` 만 import
# - 실행 방법 (PL 승인 후, 개발 머신 로컬):
#     cd packages/qa-pipeline-multitenant/qa-pipeline
#     ~/.conda/envs/py313/python.exe ../scripts/validate_state_shape.py
#
# 실패 시 exit code 1, 상세 내역은 stderr 로 출력.

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 원본 single-tenant state 의 "라우터/노드 진입 시 기대" key 집합
# (packages/agentcore-agents/qa-pipeline/routers/evaluate.py 의 initial_state)
# ---------------------------------------------------------------------------

EXPECTED_SINGLE_TENANT_KEYS: frozenset[str] = frozenset({
    "transcript",
    "consultation_type",
    "customer_id",
    "session_id",
    "llm_backend",
    "bedrock_model_id",
    "current_phase",
    # 라우터 setdefault 보강분
    "evaluations",
    "completed_nodes",
    "node_timings",
    "next_node",
})

# 멀티테넌트 추가 키
MULTITENANT_EXTRA_KEYS: frozenset[str] = frozenset({"tenant"})


# ---------------------------------------------------------------------------
# Dev1 라우터 setdefault 흉내 — 실제 라우터 코드를 import 하지 않고
# 집계 필드 기본값만 추가한다 (routers/evaluate.py::_build_initial_state 참조)
# ---------------------------------------------------------------------------


def _apply_router_setdefault(seed: dict) -> dict:
    seed.setdefault("evaluations", [])
    seed.setdefault("completed_nodes", [])
    seed.setdefault("node_timings", [])
    seed.setdefault("next_node", "")
    return seed


def main() -> int:
    # --- sys.path 세팅 (qa-pipeline 경로를 추가) -------------------------
    script_dir = Path(__file__).resolve().parent
    qa_pipeline_dir = script_dir.parent / "qa-pipeline"
    if not qa_pipeline_dir.exists():
        print(f"[ERROR] qa-pipeline dir not found: {qa_pipeline_dir}", file=sys.stderr)
        return 1
    sys.path.insert(0, str(qa_pipeline_dir))
    # 의도치 않은 boto3 접근 방지를 위해 AWS env 를 비워둔다 (영향 없지만 명시).
    os.environ.pop("AWS_PROFILE", None)

    # --- state.py import --------------------------------------------------
    try:
        from state import build_initial_state, require_tenant
    except Exception as exc:  # pragma: no cover — import 실패도 예상 밖
        print(f"[ERROR] failed to import state module: {exc}", file=sys.stderr)
        return 1

    # --- Case A: tenant_config 명시 주입 (DynamoDB 조회 skip) ------------
    seed_a = build_initial_state(
        tenant_id="kolon_default",
        tenant_config={"display_name": "Kolon", "industry": "industrial"},
        request_id="req-validate-1",
        transcript="상담사: 안녕하세요\n고객: 네, 안녕하세요",
        consultation_type="general",
        customer_id="cust-001",
        session_id="sess-001",
        llm_backend="bedrock",
        bedrock_model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
    )

    # tenant 가드 헬퍼 동작 확인 (require_tenant)
    try:
        tenant_ctx = require_tenant(seed_a)
    except Exception as exc:
        print(f"[ERROR] require_tenant rejected happy-path state: {exc}", file=sys.stderr)
        return 1
    if tenant_ctx["tenant_id"] != "kolon_default":
        print(f"[ERROR] tenant_id mismatch: {tenant_ctx!r}", file=sys.stderr)
        return 1

    # --- Case A after router setdefault ----------------------------------
    seed_a_full = _apply_router_setdefault(dict(seed_a))
    actual_keys = frozenset(seed_a_full.keys())
    expected_keys = EXPECTED_SINGLE_TENANT_KEYS | MULTITENANT_EXTRA_KEYS

    missing = expected_keys - actual_keys
    unexpected = actual_keys - expected_keys
    errors: list[str] = []
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
    if unexpected:
        errors.append(f"unexpected keys: {sorted(unexpected)}")

    # tenant 필드 구조 검증
    tenant_obj = seed_a_full.get("tenant")
    if not isinstance(tenant_obj, dict):
        errors.append(f"tenant must be dict, got {type(tenant_obj).__name__}")
    else:
        for tkey in ("tenant_id", "config", "request_id"):
            if tkey not in tenant_obj:
                errors.append(f"tenant.{tkey} missing")
        if not isinstance(tenant_obj.get("config"), dict):
            errors.append(f"tenant.config must be dict, got {type(tenant_obj.get('config')).__name__}")

    # --- Case B: require_tenant 거부 케이스 -------------------------------
    bad_cases = [
        ({}, "no tenant field"),
        ({"tenant": {}}, "empty tenant dict"),
        ({"tenant": {"tenant_id": ""}}, "empty tenant_id"),
        ({"tenant": {"tenant_id": None}}, "None tenant_id"),
    ]
    for bad_state, label in bad_cases:
        try:
            require_tenant(bad_state)  # type: ignore[arg-type]
        except ValueError:
            continue
        errors.append(f"require_tenant did not raise for case '{label}'")

    # --- 결과 출력 --------------------------------------------------------
    if errors:
        print("[FAIL] state shape validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print("[OK] state shape matches expected layout")
    print(f"  keys = {sorted(actual_keys)}")
    print(f"  tenant keys = {sorted(tenant_obj.keys())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
