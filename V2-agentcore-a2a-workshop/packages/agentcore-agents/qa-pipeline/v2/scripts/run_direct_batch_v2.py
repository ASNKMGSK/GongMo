# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""V2 Direct batch runner — V1 `scripts/run_direct_batch.py` 의 V2 포팅.

V1 `graph.py::build_graph()` → V2 `graph_v2.py::build_graph_v2()` 로만 전환.
나머지 환경변수 / 동시성 / 타임아웃 / 출력 포맷은 V1 과 동일.

환경변수:
  BATCH_MAX_CONCURRENT  (기본 2 — Bedrock throttle 완화)
  BATCH_OUTPUT_SUFFIX   (기본 "v2_direct")
  BEDROCK_MODEL_ID      (기본 Sonnet-4)
  PER_SAMPLE_TIMEOUT    (기본 600 초)
  LOG_LEVEL             (기본 INFO)
  SKIP_PHASE_C_REPORTING (기본 True — 프롬프트 튜닝 배치용)

사용 예:
  python v2/scripts/run_direct_batch_v2.py
  BATCH_OUTPUT_SUFFIX=v2_iter01 python v2/scripts/run_direct_batch_v2.py
  SKIP_PHASE_C_REPORTING=0 python v2/scripts/run_direct_batch_v2.py   # Layer 4 까지 실행
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path


# qa-pipeline 루트를 sys.path 에 추가
_PIPELINE_DIR = Path(__file__).parent.parent.parent.resolve()
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))


from v2.graph_v2 import build_graph_v2  # noqa: E402
from nodes.skills.reconciler import reconcile_evaluation  # type: ignore[import-untyped]  # noqa: E402


SAMPLES_DIR = Path(r"C:\Users\META M\Desktop\Re-qa샘플데이터\학습용")
OUTPUT_ROOT = Path(r"C:\Users\META M\Desktop\프롬프트 튜닝")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
MAX_CONCURRENT = int(os.environ.get("BATCH_MAX_CONCURRENT", "2"))
SUFFIX = os.environ.get("BATCH_OUTPUT_SUFFIX", "v2_direct")
PER_SAMPLE_TIMEOUT = float(os.environ.get("PER_SAMPLE_TIMEOUT", "600"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
SKIP_PHASE_C = os.environ.get("SKIP_PHASE_C_REPORTING", "1").lower() in ("1", "true", "yes")


logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("run_direct_batch_v2")


def extract_sample_id(filename: str) -> str:
    m = re.match(r"^(\d{6})", filename)
    if not m:
        raise ValueError(f"샘플 ID(6자리) 추출 실패: {filename}")
    return m.group(1)


def build_initial_state(transcript: str, sample_id: str) -> dict:
    """QAStateV2 초기 상태. V1 build_initial_state 와 유사 구조.

    3단계 멀티테넌트 (2026-04-24): site_id/channel/department env override 지원.
      BATCH_SITE_ID   (기본 "generic")
      BATCH_CHANNEL   (기본 "inbound")
      BATCH_DEPARTMENT (기본 "default")
    """
    import os as _os
    site_id = _os.environ.get("BATCH_SITE_ID") or "generic"
    channel = _os.environ.get("BATCH_CHANNEL") or "inbound"
    department = _os.environ.get("BATCH_DEPARTMENT") or "default"
    return {
        "transcript": transcript,
        "consultation_id": sample_id,
        "session_id": f"v2-{sample_id}-{int(time.time())}",
        "customer_id": sample_id,
        "site_id": site_id,
        "channel": channel,
        "department": department,
        "tenant_key": f"{site_id}:{channel}:{department}",
        "tenant_id": site_id,  # 레거시 alias
        "llm_backend": "bedrock",
        "bedrock_model_id": BEDROCK_MODEL_ID,
        "stt_metadata": {
            "transcription_confidence": 0.95,  # 실제 STT 연동 시 교체
            "speaker_diarization_success": True,
            "duration_sec": 180.0,
            "has_timestamps": False,
            "masking_format": {"version": "v1_symbolic"},
        },
        "plan": {
            "skip_phase_c_and_reporting": SKIP_PHASE_C,  # 기본 True — 튜닝용
        },
        "evaluated_at": datetime.utcnow().isoformat() + "Z",
    }


def extract_result(final_state: dict) -> dict:
    """final_state 에서 출력용 dict 추출.

    V1 과 동일하게 `reconcile_evaluation` 인프라 폴백 정화를 거친 후 저장.
    """
    evaluations = final_state.get("evaluations", []) or []
    reconciled = []
    for e in evaluations:
        if isinstance(e, dict):
            fixed, note = reconcile_evaluation(e)
            reconciled.append(fixed)
            if note:
                logger.debug("reconcile: %s", note)

    orchestrator = final_state.get("orchestrator") or {}
    return {
        "preprocessing": final_state.get("preprocessing"),
        "evaluations": reconciled,
        "orchestrator": orchestrator,
        "final_score": orchestrator.get("final_score"),
        "overrides": orchestrator.get("overrides"),
        "consistency_flags": orchestrator.get("consistency_flags"),
        "report": final_state.get("report"),
        "routing": final_state.get("routing"),
        "node_timings": final_state.get("node_timings"),
        "error": final_state.get("error"),
    }


async def process_sample(
    graph,
    sample_path: Path,
    output_dir: Path,
    semaphore: asyncio.Semaphore,
) -> tuple[str, bool, float]:
    """단일 샘플 실행 + 결과 저장."""
    sample_id = extract_sample_id(sample_path.name)
    output_file = output_dir / f"{sample_id}_result.json"

    if output_file.exists():
        logger.info("[%s] 이미 존재 — skip", sample_id)
        return sample_id, True, 0.0

    async with semaphore:
        transcript = sample_path.read_text(encoding="utf-8")
        initial = build_initial_state(transcript, sample_id)

        t0 = time.time()
        try:
            final_state = await asyncio.wait_for(
                graph.ainvoke(initial),
                timeout=PER_SAMPLE_TIMEOUT,
            )
            elapsed = time.time() - t0
        except asyncio.TimeoutError:
            logger.error("[%s] TIMEOUT after %.0fs", sample_id, PER_SAMPLE_TIMEOUT)
            return sample_id, False, PER_SAMPLE_TIMEOUT
        except Exception as e:
            logger.exception("[%s] 실패: %s", sample_id, e)
            return sample_id, False, time.time() - t0

        result = extract_result(final_state)
        result["_meta"] = {
            "sample_id": sample_id,
            "elapsed_sec": round(elapsed, 2),
            "skip_phase_c_and_reporting": SKIP_PHASE_C,
            "pipeline": "v2",
        }
        output_file.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("[%s] 완료 (%.1fs)", sample_id, elapsed)
        return sample_id, True, elapsed


async def main() -> None:
    if not SAMPLES_DIR.exists():
        logger.error("샘플 디렉토리 없음: %s", SAMPLES_DIR)
        sys.exit(1)

    sample_files = sorted(SAMPLES_DIR.glob("*.txt"))
    if not sample_files:
        logger.error("샘플 파일 없음: %s/*.txt", SAMPLES_DIR)
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUT_ROOT / f"batch_{ts}_{SUFFIX}"
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("출력 폴더: %s", output_dir)
    logger.info("샘플 %d 건 / 동시 %d / 타임아웃 %.0fs / skip_phase_c=%s",
                len(sample_files), MAX_CONCURRENT, PER_SAMPLE_TIMEOUT, SKIP_PHASE_C)

    graph = build_graph_v2()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    tasks = [process_sample(graph, p, output_dir, semaphore) for p in sample_files]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    # 요약 로그
    successes = sum(1 for _, ok, _ in results if ok)
    failures = len(results) - successes
    total_time = sum(elapsed for _, _, elapsed in results)
    logger.info("완료: %d 성공 / %d 실패 / 합계 %.0fs", successes, failures, total_time)

    # 실행 로그 요약 파일
    log_file = output_dir / "_run_log.md"
    lines = [
        f"# V2 Batch Run — {ts}",
        f"- 샘플: {len(sample_files)} 건",
        f"- 성공: {successes} / 실패: {failures}",
        f"- skip_phase_c_and_reporting: {SKIP_PHASE_C}",
        f"- BATCH_MAX_CONCURRENT: {MAX_CONCURRENT}",
        f"- BEDROCK_MODEL_ID: {BEDROCK_MODEL_ID}",
        "",
        "| sample | status | elapsed |",
        "|---|---|---|",
    ]
    for sid, ok, el in results:
        lines.append(f"| {sid} | {'✅' if ok else '❌'} | {el:.1f}s |")
    log_file.write_text("\n".join(lines), encoding="utf-8")
    logger.info("로그: %s", log_file)


if __name__ == "__main__":
    asyncio.run(main())
