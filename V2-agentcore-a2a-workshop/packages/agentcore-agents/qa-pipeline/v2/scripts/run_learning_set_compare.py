# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""학습셋(json) 일괄 실행 — 사람 정답지 xlsx 와 비교 리포트 생성.

- 입력 샘플: C:/Users/META M/Desktop/qa 샘플/학습셋/*.json  (transcript 필드)
- 정답 xlsx: C:/Users/META M/Desktop/QA정답-STT_기반_통합_상담평가표_v3재평가_fixed.xlsx
- 실행 모드: 3-Persona 앙상블 (기본값, QA_FORCE_SINGLE_PERSONA 미설정 상태)
- 출력: C:/Users/META M/Desktop/학습셋_비교분석_<ts>/  (결과 json + 비교 xlsx)
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
from typing import Any

_PIPELINE_DIR = Path(__file__).parent.parent.parent.resolve()
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))


from v2.graph_v2 import build_graph_v2  # noqa: E402
from nodes.skills.reconciler import reconcile_evaluation  # type: ignore[import-untyped]  # noqa: E402


_DATASET = os.environ.get("DATASET", "학습셋")  # "학습셋" or "테스트셋"
SAMPLES_DIR = Path(r"C:\Users\META M\Desktop\qa 샘플") / _DATASET
XLSX_PATH = Path(r"C:\Users\META M\Desktop\QA정답-STT_기반_통합_상담평가표_v3재평가_fixed.xlsx")
OUTPUT_ROOT = Path(r"C:\Users\META M\Desktop")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
MAX_CONCURRENT = int(os.environ.get("BATCH_MAX_CONCURRENT", "3"))
PER_SAMPLE_TIMEOUT = float(os.environ.get("PER_SAMPLE_TIMEOUT", "900"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()


logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("run_learning_set_compare")


def extract_sample_id(filename: str) -> str:
    m = re.match(r"^(\d{6})", filename)
    if not m:
        raise ValueError(f"샘플 ID(6자리) 추출 실패: {filename}")
    return m.group(1)


def build_initial_state(transcript: str, sample_id: str) -> dict:
    return {
        "transcript": transcript,
        "consultation_id": sample_id,
        "session_id": f"v2-{sample_id}-{int(time.time())}",
        "customer_id": sample_id,
        "tenant_id": "generic",
        "llm_backend": "bedrock",
        "bedrock_model_id": BEDROCK_MODEL_ID,
        "stt_metadata": {
            "transcription_confidence": 0.95,
            "speaker_diarization_success": True,
            "duration_sec": 180.0,
            "has_timestamps": False,
            "masking_format": {"version": "v1_symbolic"},
        },
        "plan": {
            # Phase C (consistency / report) 스킵 — 비교용이므로 핵심 평가만 필요
            "skip_phase_c_and_reporting": True,
        },
        "evaluated_at": datetime.utcnow().isoformat() + "Z",
    }


def extract_result(final_state: dict) -> dict:
    evaluations = final_state.get("evaluations", []) or []
    reconciled = []
    for e in evaluations:
        if isinstance(e, dict):
            fixed, _note = reconcile_evaluation(e)
            reconciled.append(fixed)
    orchestrator = final_state.get("orchestrator") or {}
    return {
        "preprocessing": final_state.get("preprocessing"),
        "evaluations": reconciled,
        "orchestrator": orchestrator,
        "final_score": orchestrator.get("final_score"),
    }


async def process_sample(
    graph,
    sample_path: Path,
    output_dir: Path,
    semaphore: asyncio.Semaphore,
) -> tuple[str, bool, float]:
    sample_id = extract_sample_id(sample_path.name)
    output_file = output_dir / f"{sample_id}_result.json"
    if output_file.exists():
        logger.info("[%s] skip (이미 존재)", sample_id)
        return sample_id, True, 0.0

    async with semaphore:
        data = json.loads(sample_path.read_text(encoding="utf-8"))
        transcript = data.get("transcript", "")
        if not transcript.strip():
            logger.error("[%s] transcript 비어있음", sample_id)
            return sample_id, False, 0.0

        initial = build_initial_state(transcript, sample_id)
        t0 = time.time()
        try:
            final_state = await asyncio.wait_for(
                graph.ainvoke(initial),
                timeout=PER_SAMPLE_TIMEOUT,
            )
            elapsed = time.time() - t0
        except asyncio.TimeoutError:
            logger.error("[%s] TIMEOUT %.0fs", sample_id, PER_SAMPLE_TIMEOUT)
            return sample_id, False, PER_SAMPLE_TIMEOUT
        except Exception as e:
            logger.exception("[%s] 실패: %s", sample_id, e)
            return sample_id, False, time.time() - t0

        result = extract_result(final_state)
        result["_meta"] = {"sample_id": sample_id, "elapsed_sec": round(elapsed, 2)}
        output_file.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("[%s] 완료 (%.1fs)", sample_id, elapsed)
        return sample_id, True, elapsed


async def main() -> Path:
    if not SAMPLES_DIR.exists():
        logger.error("샘플 디렉토리 없음: %s", SAMPLES_DIR)
        sys.exit(1)

    sample_files = sorted(SAMPLES_DIR.glob("*.json"))
    if not sample_files:
        logger.error("샘플 없음: %s/*.json", SAMPLES_DIR)
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUT_ROOT / f"{_DATASET}_비교분석_{ts}"
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("출력: %s", output_dir)
    logger.info("샘플 %d 건 / 동시 %d / 타임아웃 %.0fs",
                len(sample_files), MAX_CONCURRENT, PER_SAMPLE_TIMEOUT)

    graph = build_graph_v2()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    tasks = [process_sample(graph, p, output_dir, semaphore) for p in sample_files]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    successes = sum(1 for _, ok, _ in results if ok)
    failures = len(results) - successes
    total_time = sum(elapsed for _, _, elapsed in results)
    logger.info("완료: %d 성공 / %d 실패 / 합계 %.0fs", successes, failures, total_time)

    (output_dir / "_run_log.md").write_text(
        "\n".join(
            [f"# 학습셋 배치 — {ts}",
             f"- 샘플: {len(sample_files)} 건",
             f"- 성공: {successes} / 실패: {failures}",
             f"- 모델: {BEDROCK_MODEL_ID}",
             "",
             "| sample | status | elapsed |",
             "|---|---|---|",
             *[f"| {s} | {'OK' if ok else 'FAIL'} | {e:.1f}s |" for s, ok, e in results]]
        ),
        encoding="utf-8",
    )
    print(str(output_dir))
    return output_dir


if __name__ == "__main__":
    asyncio.run(main())
