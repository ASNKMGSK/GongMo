# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""run_id 단위 메트릭 적재.

`record_run` 은 두 가지 경로를 지원:
 1. `from_results_dir` 지정 — 이미 배치된 결과 디렉토리(`*_result.json`) 로부터
    GT xlsx 비교 메트릭 계산 + 적재 (배치 재실행 없음).
 2. `from_results_dir` 미지정 — `samples_dir` 의 transcript JSON 들로 v2 graph
    배치 평가 후 결과 + 메트릭 적재 (`run_learning_set_compare.py` 와 동일 흐름).

산출물: `<TRACKING_ROOT>/<YYYYMMDD_HHMMSS>__<label>/`
  - meta.json           : timestamp / label / git_sha / dataset / model / sample 수
  - metrics.json        : overall + per-item + per-sample 메트릭
  - results/            : 샘플별 raw json (모드 2 에서만 생성, 모드 1 은 reference 만)
  - summary.md          : 사람이 읽는 요약
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# 동급 디렉토리에 있는 메트릭 helper 재사용 (compare_learning_set_vs_xlsx.py)
_PIPELINE_DIR = Path(__file__).resolve().parents[3]
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

from v2.scripts.compare_learning_set_vs_xlsx import (  # type: ignore[import-untyped]  # noqa: E402
    ITEM_DEF, ITEM_NUM_TO_MAX, ITEM_NUM_TO_NAME,
    TEST_IDS, TRAINING_IDS,
    compute_metrics, load_ai_results, load_xlsx_ground_truth,
)
from v2.validation.tracking.config import DATASETS, TRACKING_ROOT, default_xlsx_path  # noqa: E402


logger = logging.getLogger(__name__)


@dataclass
class RunRecord:
    run_id: str
    label: str
    timestamp: str         # ISO-8601
    git_sha: str | None
    dataset: str           # "training" | "test" | "custom"
    model: str
    sample_count: int
    matched_count: int
    overall: dict[str, Any]
    per_item: dict[int, dict[str, Any]]
    per_sample: dict[str, dict[str, Any]]   # {sid: {ai_total, human_total, max_total, diff, n_items}}
    notes: str = ""
    parent_run: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# ───────────────────────────────────────────────────────────────────────
# 메트릭 계산 (results_dir → RunRecord 핵심 필드)
# ───────────────────────────────────────────────────────────────────────


def _compute_record_fields(
    results_dir: Path,
    xlsx_path: Path,
    *,
    allowed_ids: list[str] | None = None,
) -> dict[str, Any]:
    """results_dir + GT xlsx → overall/per_item/per_sample dict."""
    if allowed_ids is None:
        # 디렉토리 내 *_result.json 기반 ID 추출
        allowed_ids = sorted({p.stem.replace("_result", "") for p in results_dir.glob("*_result.json")})
    gt = load_xlsx_ground_truth(xlsx_path, allowed_ids)
    ai = load_ai_results(results_dir, allowed_ids)
    matched = sorted(set(gt.keys()) & set(ai.keys()))

    per_item_pairs: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    all_pairs: list[tuple[int, int, int]] = []
    per_sample: dict[str, dict[str, Any]] = {}

    for sid in matched:
        a_map = ai[sid]
        h_map = gt[sid]
        ai_total = 0
        human_total = 0
        max_total = 0
        n_items = 0
        for num, _name, maxs, _row in ITEM_DEF:
            if num not in a_map or num not in h_map:
                continue
            a = a_map[num]
            h = h_map[num]
            per_item_pairs[num].append((a, h, maxs))
            all_pairs.append((a, h, maxs))
            ai_total += a
            human_total += h
            max_total += maxs
            n_items += 1
        per_sample[sid] = {
            "ai_total": ai_total,
            "human_total": human_total,
            "max_total": max_total,
            "diff": ai_total - human_total,
            "n_items": n_items,
        }

    overall = compute_metrics(all_pairs)
    per_item: dict[int, dict[str, Any]] = {}
    for num, _name, _mx, _row in ITEM_DEF:
        m = compute_metrics(per_item_pairs.get(num, []))
        m["item_name"] = ITEM_NUM_TO_NAME.get(num)
        m["max_score"] = ITEM_NUM_TO_MAX.get(num)
        per_item[num] = m

    return {
        "sample_count": len(allowed_ids),
        "matched_count": len(matched),
        "overall": overall,
        "per_item": per_item,
        "per_sample": per_sample,
    }


# ───────────────────────────────────────────────────────────────────────
# 배치 실행 (mode 2 — samples_dir 부터)
# ───────────────────────────────────────────────────────────────────────


async def _run_batch(
    samples_dir: Path,
    output_dir: Path,
    *,
    bedrock_model_id: str,
    max_concurrent: int = 3,
    per_sample_timeout: float = 900.0,
) -> tuple[int, int]:
    """샘플 디렉토리 → graph_v2 배치 실행, output_dir 에 *_result.json 적재."""
    import asyncio
    import re

    from v2.graph_v2 import build_graph_v2  # type: ignore[import-untyped]
    from nodes.skills.reconciler import reconcile_evaluation  # type: ignore[import-untyped]

    sample_files = sorted(samples_dir.glob("*.json"))
    if not sample_files:
        raise RuntimeError(f"샘플 없음: {samples_dir}/*.json")

    output_dir.mkdir(parents=True, exist_ok=True)
    graph = build_graph_v2()
    semaphore = asyncio.Semaphore(max_concurrent)

    def _build_initial(transcript: str, sid: str) -> dict:
        return {
            "transcript": transcript,
            "consultation_id": sid,
            "session_id": f"track-{sid}-{int(time.time())}",
            "customer_id": sid,
            "tenant_id": "generic",
            "llm_backend": "bedrock",
            "bedrock_model_id": bedrock_model_id,
            "gt_sample_id": sid,
            "stt_metadata": {
                "transcription_confidence": 0.95,
                "speaker_diarization_success": True,
                "duration_sec": 180.0,
                "has_timestamps": False,
                "masking_format": {"version": "v1_symbolic"},
            },
            "plan": {"skip_phase_c_and_reporting": True},
            "evaluated_at": datetime.utcnow().isoformat() + "Z",
        }

    async def _process_one(jp: Path) -> tuple[str, bool]:
        m = re.match(r"^(\d{4,})", jp.name)
        if not m:
            return jp.name, False
        sid = m.group(1)
        out = output_dir / f"{sid}_result.json"
        if out.exists():
            return sid, True
        async with semaphore:
            try:
                data = json.loads(jp.read_text(encoding="utf-8"))
                transcript = data.get("transcript", "")
                if not transcript.strip():
                    return sid, False
                initial = _build_initial(transcript, sid)
                final_state = await asyncio.wait_for(
                    graph.ainvoke(initial), timeout=per_sample_timeout,
                )
                evals = final_state.get("evaluations") or []
                reconciled = []
                for e in evals:
                    if isinstance(e, dict):
                        fixed, _ = reconcile_evaluation(e)
                        reconciled.append(fixed)
                payload = {
                    "evaluations": reconciled,
                    "orchestrator": final_state.get("orchestrator") or {},
                    "preprocessing": final_state.get("preprocessing"),
                    "gt_comparison": final_state.get("gt_comparison"),
                    "gt_evidence_comparison": final_state.get("gt_evidence_comparison"),
                }
                out.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                return sid, True
            except Exception as e:
                logger.exception("[%s] 배치 실패: %s", sid, e)
                return sid, False

    results = await asyncio.gather(*[_process_one(p) for p in sample_files])
    ok = sum(1 for _, k in results if k)
    return ok, len(sample_files)


# ───────────────────────────────────────────────────────────────────────
# git sha
# ───────────────────────────────────────────────────────────────────────


def _detect_git_sha(cwd: Path | None = None) -> str | None:
    cwd = cwd or _PIPELINE_DIR
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd), capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:
        pass
    return None


# ───────────────────────────────────────────────────────────────────────
# 핵심 API
# ───────────────────────────────────────────────────────────────────────


def record_run(
    *,
    label: str,
    dataset: str = "training",
    model: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
    from_results_dir: str | Path | None = None,
    samples_dir: str | Path | None = None,
    xlsx_path: str | Path | None = None,
    notes: str = "",
    parent_run: str | None = None,
    max_concurrent: int = 3,
    per_sample_timeout: float = 900.0,
) -> RunRecord:
    """단일 런 기록.

    Args:
        label: 사람이 식별할 짧은 태그 (예: "iter06", "fix-greeting-prompt", "baseline_2026-04-22").
        dataset: "training" | "test" | "custom"  — custom 일 때 samples_dir 또는 from_results_dir 필수.
        model: Bedrock model id (mode 2 배치 실행 시 사용). meta.json 에도 기록.
        from_results_dir: 이미 평가된 결과 디렉토리. 지정 시 배치 재실행 안 함 (모드 1).
        samples_dir: transcript JSON 디렉토리 (모드 2).
        xlsx_path: GT xlsx 경로. 미지정 시 default_xlsx_path() 사용.
        notes: 변경사항 메모 (예: "language 프롬프트 #6 cushion 강화").
        parent_run: 비교 기준 run_id (생략 시 trend.py 가 자동으로 직전 run 으로 연결).
    """
    xlsx = Path(xlsx_path) if xlsx_path else default_xlsx_path()
    if not xlsx.exists():
        raise FileNotFoundError(f"GT xlsx 없음: {xlsx}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "-_." else "_" for c in label)
    run_id = f"{ts}__{safe_label}"
    run_dir = TRACKING_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    results_dir = run_dir / "results"

    # ── 데이터셋 / 결과 dir 결정 ─────────────────────────────────
    allowed_ids: list[str] | None = None
    if from_results_dir:
        src = Path(from_results_dir)
        if not src.exists():
            raise FileNotFoundError(f"results_dir 없음: {src}")
        # 모드 1: 결과 reference 만 보관 (복사하지 않음 — 용량 절감)
        (run_dir / "_results_ref.txt").write_text(str(src.resolve()), encoding="utf-8")
        results_dir_eff = src
    else:
        # 모드 2: 배치 실행 필요
        ds = DATASETS.get(dataset)
        if samples_dir is None and ds is None:
            raise ValueError(f"dataset='{dataset}' 미정의 — samples_dir 직접 지정 필요")
        smp = Path(samples_dir) if samples_dir else Path(ds["samples_dir"])  # type: ignore[index]
        if not smp.exists():
            raise FileNotFoundError(f"samples_dir 없음: {smp}")
        import asyncio
        ok, total = asyncio.run(_run_batch(
            smp, results_dir,
            bedrock_model_id=model,
            max_concurrent=max_concurrent,
            per_sample_timeout=per_sample_timeout,
        ))
        logger.info("배치 완료: %d / %d", ok, total)
        results_dir_eff = results_dir

    if dataset == "training":
        allowed_ids = TRAINING_IDS
    elif dataset == "test":
        allowed_ids = TEST_IDS

    # ── 메트릭 계산 ──────────────────────────────────────────────
    fields = _compute_record_fields(results_dir_eff, xlsx, allowed_ids=allowed_ids)

    record = RunRecord(
        run_id=run_id,
        label=label,
        timestamp=datetime.now().isoformat(timespec="seconds"),
        git_sha=_detect_git_sha(),
        dataset=dataset,
        model=model,
        sample_count=fields["sample_count"],
        matched_count=fields["matched_count"],
        overall=fields["overall"],
        per_item=fields["per_item"],
        per_sample=fields["per_sample"],
        notes=notes,
        parent_run=parent_run,
        extras={"xlsx_path": str(xlsx), "results_dir": str(results_dir_eff)},
    )

    # ── 적재 ──────────────────────────────────────────────────────
    _persist(run_dir, record)
    logger.info("run 적재: %s (MAE=%s, RMSE=%s)", run_id, record.overall.get("MAE"), record.overall.get("RMSE"))
    return record


def _persist(run_dir: Path, record: RunRecord) -> None:
    meta = {
        "run_id": record.run_id,
        "label": record.label,
        "timestamp": record.timestamp,
        "git_sha": record.git_sha,
        "dataset": record.dataset,
        "model": record.model,
        "sample_count": record.sample_count,
        "matched_count": record.matched_count,
        "notes": record.notes,
        "parent_run": record.parent_run,
        "extras": record.extras,
    }
    metrics = {
        "overall": record.overall,
        "per_item": {str(k): v for k, v in record.per_item.items()},
        "per_sample": record.per_sample,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "summary.md").write_text(_render_summary_md(record), encoding="utf-8")


def _render_summary_md(r: RunRecord) -> str:
    o = r.overall
    head = (
        f"# Run {r.run_id}\n\n"
        f"- label: **{r.label}**\n"
        f"- timestamp: {r.timestamp}\n"
        f"- git_sha: `{r.git_sha or '-'}`\n"
        f"- dataset: {r.dataset} ({r.matched_count} / {r.sample_count} matched)\n"
        f"- model: `{r.model}`\n"
        f"- notes: {r.notes or '-'}\n"
        f"- parent_run: `{r.parent_run or '-'}`\n\n"
        f"## Overall\n\n"
        f"| n | MAE | RMSE | Bias | MAPE | MaxAbs | Over% | Under% |\n"
        f"|--:|--:|--:|--:|--:|--:|--:|--:|\n"
        f"| {o.get('n')} | {o.get('MAE')} | {o.get('RMSE')} | {o.get('Bias')} | "
        f"{o.get('MAPE')} | {o.get('MaxAbs')} | {o.get('Over%')} | {o.get('Under%')} |\n\n"
        f"## Per-item\n\n"
        f"| # | 항목 | n | MAE | RMSE | Bias | MAPE |\n"
        f"|--:|---|--:|--:|--:|--:|--:|\n"
    )
    rows = []
    for num, _name, _mx, _row in ITEM_DEF:
        m = r.per_item.get(num, {})
        rows.append(
            f"| {num} | {m.get('item_name','')} | {m.get('n')} | "
            f"{m.get('MAE')} | {m.get('RMSE')} | {m.get('Bias')} | {m.get('MAPE')} |"
        )
    return head + "\n".join(rows) + "\n"


# ───────────────────────────────────────────────────────────────────────
# 로더
# ───────────────────────────────────────────────────────────────────────


def load_record(run_dir: Path | str) -> RunRecord:
    rd = Path(run_dir)
    meta = json.loads((rd / "meta.json").read_text(encoding="utf-8"))
    metrics = json.loads((rd / "metrics.json").read_text(encoding="utf-8"))
    return RunRecord(
        run_id=meta["run_id"],
        label=meta["label"],
        timestamp=meta["timestamp"],
        git_sha=meta.get("git_sha"),
        dataset=meta.get("dataset", "custom"),
        model=meta.get("model", ""),
        sample_count=meta.get("sample_count", 0),
        matched_count=meta.get("matched_count", 0),
        overall=metrics.get("overall", {}),
        per_item={int(k): v for k, v in metrics.get("per_item", {}).items()},
        per_sample=metrics.get("per_sample", {}),
        notes=meta.get("notes", ""),
        parent_run=meta.get("parent_run"),
        extras=meta.get("extras", {}),
    )


def delete_run(run_id: str) -> bool:
    rd = TRACKING_ROOT / run_id
    if not rd.exists():
        return False
    shutil.rmtree(rd)
    return True
