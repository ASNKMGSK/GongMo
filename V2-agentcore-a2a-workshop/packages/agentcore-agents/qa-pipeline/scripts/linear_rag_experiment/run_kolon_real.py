# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
진짜 코오롱 데이터 LinearRAG vs Baseline — Multi-label retrieval 실험.

Multi-KMS 케이스 (한 상담 → 여러 KMS 적용) 측정에 특화:

  Metric:
    - Intent Precision@K : top-K KMS 중 GT intent 에 속한 비율
    - Intent Recall@K   : GT intent 중 top-K 가 커버한 비율
    - Intent F1@K       : 둘의 조화평균
    - Multi-KMS 케이스 (gt_intents >= 2) 별도 분리 측정

쿼리 전략:
  ─ 'description' : 파일 description 한 줄을 쿼리로 (가장 단순)
  ─ 'transcript'  : transcript 전체를 쿼리로 (현실적이나 길이 부담)
  ─ 'first_turns' : transcript 처음 N turns 만 (절충안, 기본)

환경변수:
  EXP_QUERY_MODE : description | first_turns | transcript (기본: first_turns)
  EXP_TOP_K      : 5 (기본)
  AWS_REGION     : Bedrock 리전
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

DATA_DIR = SCRIPT_DIR / "data"
RESULT_DIR = SCRIPT_DIR / "results"
RESULT_DIR.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("QA_LINEAR_NER", "auto")  # Kiwi 기본


# ── 헬퍼 ──────────────────────────────────────────────────────────────


def load_data():
    kms = json.loads((DATA_DIR / "kolon_kms.json").read_text(encoding="utf-8"))
    cases = json.loads((DATA_DIR / "kolon_test_cases.json").read_text(encoding="utf-8"))
    return kms, cases


def get_embed_fn():
    import boto3

    region = os.environ.get("AWS_REGION") or "us-east-1"
    client = boto3.client("bedrock-runtime", region_name=region)
    cache: dict[str, tuple[float, ...]] = {}

    def embed(text: str):
        if not text or not text.strip():
            return None
        key = text[:8000]
        if key in cache:
            return cache[key]
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
            cache[key] = t
            return t
        except Exception as e:  # noqa: BLE001
            print(f"  embed fail: {e}", file=sys.stderr)
            return None

    return embed, cache


def build_query(case: dict, mode: str) -> str:
    """case 에서 검색 쿼리 추출."""
    if mode == "description":
        return case.get("description") or ""
    elif mode == "transcript":
        return case.get("transcript") or ""
    else:  # first_turns — 처음 ~10 turns (~1500 chars)
        ts = case.get("transcript") or ""
        # 약 1500 자 또는 첫 10 turns
        return ts[:1500]


# ── Multi-label metric ────────────────────────────────────────────────


def compute_multilabel_metrics(predictions: list[dict], k: int = 5) -> dict:
    """predictions: [{case_id, gt_intents, top_intents (top-k retrieved 의 intent 셋)}]"""
    n = len(predictions)
    if n == 0:
        return {}
    p_sum = r_sum = f1_sum = 0.0
    intent_hit = 0
    intent_total = 0
    fully_covered = 0
    for p in predictions:
        gt = set(p["gt_intents"])
        top_intents = set(p["top_intents"])
        if not gt:
            continue
        # intent precision = (top intent ∩ gt) / |top intent|
        # intent recall = (top intent ∩ gt) / |gt|
        inter = gt & top_intents
        prec = len(inter) / max(1, len(top_intents))
        rec = len(inter) / max(1, len(gt))
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
        p_sum += prec
        r_sum += rec
        f1_sum += f1
        intent_hit += len(inter)
        intent_total += len(gt)
        if inter == gt:
            fully_covered += 1
    return {
        "n": n,
        "k": k,
        "precision": p_sum / n,
        "recall": r_sum / n,
        "f1": f1_sum / n,
        "macro_recall_intents": intent_hit / max(1, intent_total),
        "fully_covered_rate": fully_covered / n,
    }


# ── Baseline ─────────────────────────────────────────────────────────


def run_baseline(kms_rows, cases, embed_fn, k: int, query_mode: str):
    from scripts.linear_rag_experiment.baseline_vector_rag import BaselineVectorRAG

    print("\n=== Baseline Vector RAG (real Kolon KMS) ===")
    rag = BaselineVectorRAG(embed_fn=embed_fn)
    t0 = time.perf_counter()
    rag.index(kms_rows)
    idx_time = time.perf_counter() - t0
    print(f"Indexing: {idx_time:.2f}s ({len(kms_rows)} KMS rows)")

    predictions = []
    latencies = []
    for case in cases:
        q = build_query(case, query_mode)
        t1 = time.perf_counter()
        results = rag.retrieve(q, top_k=k)
        latencies.append(time.perf_counter() - t1)
        # top-K KMS 의 intent 셋
        top_intents: list[str] = []
        for r in results:
            intent = r.metadata.get("intent")
            if intent and not r.metadata.get("is_evaluation_skip"):
                top_intents.append(intent)
        # dedupe 유지 순서
        top_intents = list(dict.fromkeys(top_intents))
        predictions.append(
            {
                "case_id": case["case_id"],
                "filename": case["filename"],
                "split": case["split"],
                "gt_intents": case["gt_intents"],
                "top_pids": [r.pid for r in results],
                "top_intents": top_intents,
                "top_scores": [r.score for r in results],
                "is_multi_kms": len(case["gt_intents"]) >= 2,
            }
        )

    return {
        "system": "baseline_vector",
        "query_mode": query_mode,
        "k": k,
        "index_time_s": idx_time,
        "predictions": predictions,
        "latencies_s": latencies,
        "metrics": compute_multilabel_metrics(predictions, k),
    }


# ── LinearRAG ────────────────────────────────────────────────────────


def run_linear(kms_rows, cases, embed_fn, k: int, query_mode: str, tenant_root: Path):
    from v2.rag.linear_rag import LinearRAG, build_index, kms_table_to_corpus

    print("\n=== LinearRAG (real Kolon KMS) ===")
    items = kms_table_to_corpus(kms_rows)
    if tenant_root.exists():
        shutil.rmtree(tenant_root)
    tenant_root.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    build = build_index(
        tenant_id="kolon", corpus=items, tenant_root=tenant_root, embed_fn=embed_fn
    )
    idx_time = time.perf_counter() - t0
    stats = build.graph_stats
    print(f"Indexing: {idx_time:.2f}s")
    print(f"Tri-Graph: passages={stats['num_passages']}, sentences={stats['num_sentences']}, "
          f"entities={stats['num_entities']}, C_nnz={stats['C_nnz']}, M_nnz={stats['M_nnz']}")

    rag = LinearRAG(tenant_id="kolon", tenant_root=tenant_root, embed_fn=embed_fn)

    predictions = []
    latencies = []
    for case in cases:
        q = build_query(case, query_mode)
        t1 = time.perf_counter()
        result = rag.retrieve(q, top_k=k)
        latencies.append(time.perf_counter() - t1)

        # passage 의 metadata (intent, branch) 를 KMS rows 매핑으로 가져오기
        pid_to_intent = {r["pid"]: r["intent"] for r in kms_rows}
        pid_to_skip = {r["pid"]: r.get("is_evaluation_skip", False) for r in kms_rows}
        top_intents = []
        for p in result.passages:
            it = pid_to_intent.get(p.pid)
            if it and not pid_to_skip.get(p.pid):
                top_intents.append(it)
        top_intents = list(dict.fromkeys(top_intents))

        predictions.append(
            {
                "case_id": case["case_id"],
                "filename": case["filename"],
                "split": case["split"],
                "gt_intents": case["gt_intents"],
                "top_pids": [p.pid for p in result.passages],
                "top_intents": top_intents,
                "top_scores": [p.ppr_score for p in result.passages],
                "activated_entity_count": len(result.activated_entities),
                "is_multi_kms": len(case["gt_intents"]) >= 2,
            }
        )

    return {
        "system": "linear_rag",
        "query_mode": query_mode,
        "k": k,
        "index_time_s": idx_time,
        "graph_stats": stats,
        "predictions": predictions,
        "latencies_s": latencies,
        "metrics": compute_multilabel_metrics(predictions, k),
    }


# ── 리포트 ────────────────────────────────────────────────────────────


def split_by_multi(predictions):
    single = [p for p in predictions if not p["is_multi_kms"]]
    multi = [p for p in predictions if p["is_multi_kms"]]
    return single, multi


def print_report(results, k):
    print("\n" + "=" * 80)
    print(f"📊 진짜 코오롱 데이터 — LinearRAG vs Baseline (top-K={k})")
    print("=" * 80)

    print("\n## Overall Multi-label Metrics")
    h = f"{'System':<22}{'Prec@K':>10}{'Recall@K':>10}{'F1@K':>10}{'FullCov':>10}{'Index(s)':>10}{'p50ms':>10}"
    print(h)
    print("-" * len(h))
    for r in results:
        m = r["metrics"]
        lat_ms = sorted([x * 1000 for x in r["latencies_s"]])
        p50 = lat_ms[len(lat_ms) // 2] if lat_ms else 0
        print(
            f"{r['system']:<22}"
            f"{m['precision']:>10.1%}"
            f"{m['recall']:>10.1%}"
            f"{m['f1']:>10.1%}"
            f"{m['fully_covered_rate']:>10.1%}"
            f"{r['index_time_s']:>10.2f}"
            f"{p50:>10.0f}"
        )

    # multi-KMS 분리 측정
    print("\n## Multi-KMS cases (gt_intents >= 2) — 진짜 GraphRAG 강점 측정 영역")
    print(h)
    print("-" * len(h))
    for r in results:
        single, multi = split_by_multi(r["predictions"])
        if multi:
            mm = compute_multilabel_metrics(multi, k)
            print(f"{r['system'] + ' (multi)':<22}"
                  f"{mm['precision']:>10.1%}"
                  f"{mm['recall']:>10.1%}"
                  f"{mm['f1']:>10.1%}"
                  f"{mm['fully_covered_rate']:>10.1%}"
                  f"{'':>10}{'':>10}")

    print("\n## Single-KMS cases (gt_intents == 1)")
    for r in results:
        single, multi = split_by_multi(r["predictions"])
        if single:
            sm = compute_multilabel_metrics(single, k)
            print(f"{r['system'] + ' (single)':<22}"
                  f"{sm['precision']:>10.1%}"
                  f"{sm['recall']:>10.1%}"
                  f"{sm['f1']:>10.1%}"
                  f"{sm['fully_covered_rate']:>10.1%}"
                  f"{'':>10}{'':>10}")

    # 다른 답을 낸 케이스 분석
    if len(results) >= 2:
        baseline = next((r for r in results if r["system"] == "baseline_vector"), None)
        linear = next((r for r in results if r["system"] == "linear_rag"), None)
        if baseline and linear:
            print("\n## 차이가 난 케이스 (intent 셋이 다른 경우)")
            for bp, lp in zip(baseline["predictions"], linear["predictions"]):
                if bp["case_id"] != lp["case_id"]:
                    continue
                if set(bp["top_intents"][:k]) != set(lp["top_intents"][:k]):
                    gt = bp["gt_intents"]
                    multi = "📌 MULTI" if bp["is_multi_kms"] else "      "
                    print(f"\n  {multi} [{bp['case_id']}] split={bp['split']} GT={gt}")
                    print(f"    Baseline intents @{k}: {bp['top_intents'][:k]}  pids: {bp['top_pids'][:k]}")
                    print(f"    LinearRAG intents @{k}: {lp['top_intents'][:k]}  pids: {lp['top_pids'][:k]}")


def main():
    k = int(os.environ.get("EXP_TOP_K", "5"))
    query_mode = os.environ.get("EXP_QUERY_MODE", "first_turns")
    print(f"Config: k={k}, query_mode={query_mode}")

    print("Loading data...")
    kms_rows, cases = load_data()
    print(f"  KMS: {len(kms_rows)} rows")
    print(f"  Cases: {len(cases)}, multi-KMS: {sum(1 for c in cases if len(c['gt_intents']) >= 2)}")

    print("Bedrock Titan v2 init...")
    embed_fn, cache = get_embed_fn()
    if embed_fn("warmup") is None:
        print("ERROR: Bedrock 실패")
        return 1

    baseline = run_baseline(kms_rows, cases, embed_fn, k, query_mode)
    tenant_root = Path("/tmp/linear_rag_kolon_real")
    linear = run_linear(kms_rows, cases, embed_fn, k, query_mode, tenant_root)

    results = [baseline, linear]
    print_report(results, k)

    # 저장
    from datetime import datetime, timezone

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = RESULT_DIR / f"kolon_real_{query_mode}_k{k}_{timestamp}.json"
    out.write_text(
        json.dumps(
            {
                "config": {"k": k, "query_mode": query_mode},
                "n_kms": len(kms_rows),
                "n_cases": len(cases),
                "multi_kms_count": sum(1 for c in cases if len(c["gt_intents"]) >= 2),
                "embed_cache_size": len(cache),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n결과: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
