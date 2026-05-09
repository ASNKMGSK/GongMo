# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
LinearRAG (Tri-Graph) 기반 인텐트 set 분류 → LLM Set Prediction 과 F1 비교.

흐름:
  KMS 18행 corpus → Tri-Graph 인덱싱 (passage / sentence / entity)
  transcript (full) → LinearRAG retrieve (top_k passages)
  retrieved passages 의 intent metadata → ppr_score 가중 합산
  threshold 넘는 인텐트 set 반환 → Opus Gold 와 F1 측정

사용:
  cd packages/agentcore-agents/qa-pipeline
  python -X utf8 scripts/linear_rag_experiment/run_intent_via_linear_rag.py
  python -X utf8 scripts/linear_rag_experiment/run_intent_via_linear_rag.py --top_k 8 --threshold 0.4
  python -X utf8 scripts/linear_rag_experiment/run_intent_via_linear_rag.py --split 학습셋
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent  # qa-pipeline 폴더
sys.path.insert(0, str(PROJECT_ROOT))

# v2.rag.linear_rag — 사용자가 만든 Tri-Graph 기반 클린룸 LinearRAG
from v2.rag.linear_rag import LinearRAG, build_index, kms_table_to_corpus  # noqa: E402

DATA_DIR = SCRIPT_DIR / "data"
RESULTS_DIR = SCRIPT_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

KMS_PATH = DATA_DIR / "kolon_kms.json"
CASES_PATH = DATA_DIR / "kolon_test_cases.json"
GOLD_PATH = Path(r"C:\Users\META M\Desktop\QA자동평가_평가셋\kolon_intent_gold_full.json")

INTENT_OPTIONS = ["교환", "반품", "배송", "수선", "취소", "환불", "회원정보"]


def get_embed_fn():
    """Bedrock Titan Embed v2 wrapper — V3 와 동일."""
    import boto3

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    client = boto3.client("bedrock-runtime", region_name=region)
    cache: dict[str, tuple[float, ...]] = {}

    def embed(text: str):
        if not text or not text.strip():
            return None
        if text in cache:
            return cache[text]
        try:
            resp = client.invoke_model(
                modelId="amazon.titan-embed-text-v2:0",
                contentType="application/json",
                accept="application/json",
                body=json.dumps(
                    {"inputText": text[:8000], "dimensions": 1024, "normalize": True}
                ),
            )
            payload = json.loads(resp["body"].read())
            vec = payload.get("embedding")
            if not isinstance(vec, list) or len(vec) != 1024:
                return None
            t = tuple(vec)
            cache[text] = t
            return t
        except Exception as e:
            print(f"embed 실패: {e}", file=sys.stderr)
            return None

    return embed


def transcript_to_query(transcript: str, max_chars: int = 7800) -> str:
    return (transcript or "")[:max_chars]


def classify_via_linear_rag(
    rag: LinearRAG,
    transcript: str,
    top_k: int = 10,
    threshold_ratio: float = 0.3,
) -> dict:
    """LinearRAG retrieve → 인텐트 score 합산 → threshold-넘는 set 반환."""
    query = transcript_to_query(transcript)
    if not query:
        return {"intents": [], "scores": {}, "passages": []}

    result = rag.retrieve(query, top_k=top_k)

    intent_scores: dict[str, float] = defaultdict(float)
    passages_log: list[dict] = []
    for p in result.passages:
        intent = (p.metadata or {}).get("intent")
        if intent and intent in INTENT_OPTIONS:
            intent_scores[intent] += p.ppr_score
        passages_log.append({
            "pid": p.pid,
            "intent": intent,
            "branch": (p.metadata or {}).get("branch"),
            "ppr_score": round(p.ppr_score, 4),
        })

    if not intent_scores:
        return {"intents": [], "scores": {}, "passages": passages_log}

    max_score = max(intent_scores.values())
    if max_score <= 0:
        return {"intents": [], "scores": dict(intent_scores), "passages": passages_log}

    # max 의 threshold_ratio 이상이면 검출
    threshold = max_score * threshold_ratio
    detected = sorted(
        [(i, s) for i, s in intent_scores.items() if s >= threshold],
        key=lambda x: x[1],
        reverse=True,
    )
    intents = [i for i, _ in detected]

    return {
        "intents": intents,
        "scores": {i: round(s, 4) for i, s in intent_scores.items()},
        "passages": passages_log,
    }


def compute_f1(predictions: dict[str, list[str]], gold: dict[str, list[str]]) -> dict:
    """Set-level micro F1."""
    tp = fp = fn = 0
    case_results: list[dict] = []
    for case_id in sorted(set(list(predictions.keys()) + list(gold.keys()))):
        pred_set = set(predictions.get(case_id, []))
        gold_set = set(gold.get(case_id, []))
        case_tp = len(pred_set & gold_set)
        case_fp = len(pred_set - gold_set)
        case_fn = len(gold_set - pred_set)
        tp += case_tp
        fp += case_fp
        fn += case_fn
        case_results.append({
            "case_id": case_id,
            "gold": sorted(gold_set),
            "pred": sorted(pred_set),
            "tp": case_tp, "fp": case_fp, "fn": case_fn,
            "match": pred_set == gold_set,
        })

    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    full_match = sum(1 for c in case_results if c["match"])
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": round(p, 4),
        "recall": round(r, 4),
        "f1": round(f1, 4),
        "full_match": full_match,
        "n_cases": len(case_results),
        "case_results": case_results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all", choices=["학습셋", "테스트셋", "all"])
    ap.add_argument("--top_k", type=int, default=10)
    ap.add_argument("--threshold", type=float, default=0.3,
                    help="max_score 대비 비율 (이 이상이면 검출)")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--rebuild", action="store_true", help="인덱스 강제 재빌드")
    ap.add_argument("--tenant_root", default=None, help="인덱스 저장 위치 (기본 TEMP)")
    args = ap.parse_args()

    print("== LinearRAG (Tri-Graph) 인텐트 set 분류 실험 ==")
    print(f"Split: {args.split}, top_k: {args.top_k}, threshold_ratio: {args.threshold}")

    # 데이터 로드
    kms_rows = json.loads(KMS_PATH.read_text(encoding="utf-8"))
    cases_all = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases_all = [c for c in cases_all if "-" not in str(c["case_id"])]
    if args.split != "all":
        cases = [c for c in cases_all if c.get("split") == args.split]
    else:
        cases = cases_all

    gold_data = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    gold = {str(c["case_id"]): c["intents"] for c in gold_data["cases"]}

    print(f"KMS rows: {len(kms_rows)}, Cases: {len(cases)}")

    # 인덱스 위치
    default_root = Path(os.environ.get("TEMP", str(SCRIPT_DIR))) / "linear_rag_intent"
    tenant_root = Path(args.tenant_root) if args.tenant_root else default_root
    tenant_id = "intent_eval"

    if args.rebuild and tenant_root.exists():
        shutil.rmtree(tenant_root)
    tenant_root.mkdir(parents=True, exist_ok=True)

    embed_fn = get_embed_fn()
    if embed_fn("warmup") is None:
        print("ERROR: Bedrock embed 실패 — AWS 자격증명/리전 확인", file=sys.stderr)
        return 1

    # 인덱싱 (rebuild 또는 첫 실행)
    from v2.rag.linear_rag import tri_graph_exists
    need_build = args.rebuild or not tri_graph_exists(tenant_id, tenant_root)
    if need_build:
        print("Indexing KMS 18 rows into Tri-Graph...")
        items = kms_table_to_corpus(kms_rows)
        build = build_index(
            tenant_id=tenant_id,
            corpus=items,
            tenant_root=tenant_root,
            embed_fn=embed_fn,
        )
        print(f"  passages={build.graph_stats['num_passages']}, "
              f"sentences={build.graph_stats['num_sentences']}, "
              f"entities={build.graph_stats['num_entities']}")
        print(f"  Indexing time: {build.elapsed_seconds:.2f}s")
    else:
        print(f"기존 인덱스 사용: {tenant_root / tenant_id}")

    rag = LinearRAG(tenant_id=tenant_id, tenant_root=tenant_root, embed_fn=embed_fn)

    # 분류 실행
    print(f"\nClassifying {len(cases)} cases (workers={args.workers})...")
    print_lock = threading.Lock()
    t_start = time.time()

    def _process(idx: int, c: dict) -> dict:
        case_id = str(c["case_id"])
        t1 = time.time()
        try:
            out = classify_via_linear_rag(
                rag, c.get("transcript", ""),
                top_k=args.top_k, threshold_ratio=args.threshold,
            )
        except Exception as e:
            with print_lock:
                print(f"[{idx}/{len(cases)}] case={case_id} ERROR: {e}", file=sys.stderr)
            return {"case_id": case_id, "error": str(e)}
        elapsed = time.time() - t1
        gold_intents = gold.get(case_id, [])
        match_mark = "✅" if set(out["intents"]) == set(gold_intents) else "❌"
        with print_lock:
            print(f"[{idx:>2}/{len(cases)}] {match_mark} case={case_id} | "
                  f"gold={gold_intents} | pred={out['intents']} | "
                  f"scores={out['scores']} | {elapsed:.1f}s")
        return {"case_id": case_id, "split": c.get("split"), **out, "elapsed_s": round(elapsed, 2)}

    out_recs: list[dict | None] = [None] * len(cases)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(_process, i + 1, c): i for i, c in enumerate(cases)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                out_recs[i] = fut.result()
            except Exception as e:
                with print_lock:
                    print(f"[{i+1}] FATAL: {e}", file=sys.stderr)
                out_recs[i] = {"case_id": str(cases[i]["case_id"]), "error": str(e)}

    raw_results = [r for r in out_recs if r is not None]
    elapsed_total = time.time() - t_start
    print(f"\nTotal: {elapsed_total:.1f}s")

    # F1 측정
    predictions = {r["case_id"]: r.get("intents", []) for r in raw_results if "intents" in r}
    pred_in_scope = {cid: predictions[cid] for cid in (str(c["case_id"]) for c in cases) if cid in predictions}
    gold_in_scope = {cid: gold[cid] for cid in pred_in_scope.keys() if cid in gold}

    metrics = compute_f1(pred_in_scope, gold_in_scope)

    print(f"\n## 전체 F1 (n={metrics['n_cases']})")
    print(f"  TP={metrics['tp']} FP={metrics['fp']} FN={metrics['fn']}")
    print(f"  Precision={metrics['precision']:.4f}  Recall={metrics['recall']:.4f}  F1={metrics['f1']:.4f}")
    print(f"  완전 일치: {metrics['full_match']}/{metrics['n_cases']}")

    # 분할별
    by_split: dict[str, dict[str, list[str]]] = defaultdict(dict)
    by_split_gold: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for c in cases:
        cid = str(c["case_id"])
        if cid in pred_in_scope:
            by_split[c["split"]][cid] = pred_in_scope[cid]
            by_split_gold[c["split"]][cid] = gold.get(cid, [])

    print("\n## Per-split F1")
    by_split_metrics: dict[str, dict] = {}
    for sp in by_split:
        m = compute_f1(by_split[sp], by_split_gold[sp])
        by_split_metrics[sp] = m
        print(f"  {sp}: F1={m['f1']:.4f} (P={m['precision']:.4f}, R={m['recall']:.4f}) "
              f"| 완전일치 {m['full_match']}/{m['n_cases']}")

    # 인텐트 분포 (검출 vs gold)
    pred_dist: dict[str, int] = defaultdict(int)
    gold_dist: dict[str, int] = defaultdict(int)
    for cid in pred_in_scope:
        for i in pred_in_scope[cid]:
            pred_dist[i] += 1
        for i in gold_in_scope.get(cid, []):
            gold_dist[i] += 1
    print("\n## 인텐트 분포 (Gold vs LinearRAG)")
    for it in INTENT_OPTIONS:
        diff = pred_dist[it] - gold_dist[it]
        sign = "+" if diff > 0 else ""
        print(f"  {it:6s} | gold {gold_dist[it]} → pred {pred_dist[it]}  ({sign}{diff})")

    # 저장
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS_DIR / f"intent_via_linear_rag_{ts}.json"
    out_path.write_text(json.dumps({
        "timestamp": ts,
        "config": {
            "split": args.split,
            "top_k": args.top_k,
            "threshold_ratio": args.threshold,
        },
        "metrics_overall": {k: v for k, v in metrics.items() if k != "case_results"},
        "metrics_by_split": {sp: {k: v for k, v in m.items() if k != "case_results"}
                             for sp, m in by_split_metrics.items()},
        "case_results": metrics["case_results"],
        "raw_results": raw_results,
        "intent_distribution": {
            "gold": dict(gold_dist),
            "pred": dict(pred_dist),
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
