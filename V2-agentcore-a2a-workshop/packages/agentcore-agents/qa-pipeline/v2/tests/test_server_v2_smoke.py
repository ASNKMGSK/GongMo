# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""V2 FastAPI server smoke tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


_QA_PIPELINE_ROOT = Path(__file__).resolve().parents[2]
if str(_QA_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_QA_PIPELINE_ROOT))


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from v2.serving.server_v2 import app

    with TestClient(app) as c:
        yield c


def test_ping_returns_ok(client):
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_v2(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["service"] == "qa-pipeline-v2"


def test_readyz_reports_graph_status(client):
    response = client.get("/readyz")
    # graph 빌드 성공 시 200, 실패 시 503 — skeleton 환경에서는 보통 성공
    assert response.status_code in (200, 503)


def test_evaluate_requires_transcript(client):
    response = client.post("/evaluate", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"


def test_evaluate_runs_graph(client):
    response = client.post("/evaluate", json={
        "transcript": "상담사: 안녕하세요 김상담입니다.\n고객: 주문 취소\n상담사: 네 좋은 하루 되세요",
        "stt_metadata": {
            "transcription_confidence": 0.95,
            "speaker_diarization_success": True,
            "duration_sec": 60,
        },
        "consultation_id": "server-test-001",
        "tenant_id": "generic",
    })
    assert response.status_code == 200
    body = response.json()
    # preprocessing / orchestrator 존재 확인 (skeleton 단계: 값은 mock 일 수 있음)
    assert "preprocessing" in body
    assert "orchestrator" in body
    assert body["_meta"]["pipeline"] == "v2"


def test_invocations_delegates_to_evaluate(client):
    response = client.post("/invocations", json={
        "transcript": "상담사: test\n고객: test",
        "stt_metadata": {"transcription_confidence": 0.95, "speaker_diarization_success": True, "duration_sec": 30},
    })
    assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
