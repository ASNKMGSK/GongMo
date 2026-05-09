# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
LinearRAG vs Baseline Vector RAG — 실험 runner.

사용법:
    cd packages/agentcore-agents/qa-pipeline
    python -X utf8 scripts/linear_rag_experiment/run_experiment.py

환경변수:
    AWS_REGION       : Bedrock 리전 (기본 us-east-1)
    QA_LINEAR_NER    : "kiwi" | "spacy" | "regex" | "auto" (기본 auto)
    EXP_OUTPUT_DIR   : 결과 저장 디렉토리 (기본 scripts/linear_rag_experiment/results)

측정 metric:
    - Top-1 accuracy (1순위 = ground truth)
    - Top-3 accuracy (top-3 안에 ground truth 포함)
    - MRR (Mean Reciprocal Rank)
    - Indexing time
    - Retrieval latency (per-query, p50/p95)
    - Per-difficulty breakdown
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# 프로젝트 루트를 path 에 추가
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# UTF-8 stdout (Windows 한글 깨짐 방지)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "WARNING"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("experiment")

DATA_DIR = SCRIPT_DIR / "data"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "results"

# Kiwi 우선 (가벼움 + Korean NER)
os.environ.setdefault("QA_LINEAR_NER", "auto")


def load_data():
    """기본은 v2 (확장 데이터셋). EXP_DATASET=v1 환경변수로 v1 사용 가능."""
    version = os.environ.get("EXP_DATASET", "v2").strip().lower()
    if version == "v1":
        corpus_file = DATA_DIR / "kms_corpus.json"
        queries_file = DATA_DIR / "test_queries.json"
    else:
        corpus_file = DATA_DIR / "kms_corpus_v2.json"
        queries_file = DATA_DIR / "test_queries_v2.json"
    print(f"Dataset: {corpus_file.name} + {queries_file.name}")
    corpus = json.loads(corpus_file.read_text(encoding="utf-8"))
    queries = json.loads(queries_file.read_text(encoding="utf-8"))
    return corpus, queries


def get_embed_fn():
    """Bedrock Titan Embed v2 wrapper — V3 의 embedding.py 와 동일한 호출."""
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
        except Exception as e:  # noqa: BLE001
            logger.warning("Bedrock embed 실패: %s — %s", e, text[:30])
            return None

    return embed, cache


# ── Metric 계산 ───────────────────────────────────────────────────────


def compute_metrics(predictions: list[dict]) -> dict:
    """predictions: [{qid, expected_pid, top_pids: [pid1, pid2, ...], rank, ...}]"""
    n = len(predictions)
    if n == 0:
        return {}
    top1_correct = sum(1 for p in predictions if p["rank"] == 1)
    top3_correct = sum(1 for p in predictions if 1 <= p["rank"] <= 3)
    top5_correct = sum(1 for p in predictions if 1 <= p["rank"] <= 5)
    rr_sum = sum(1.0 / p["rank"] if p["rank"] > 0 else 0.0 for p in predictions)
    return {
        "n": n,
        "top1_accuracy": top1_correct / n,
        "top3_accuracy": top3_correct / n,
        "top5_accuracy": top5_correct / n,
        "mrr": rr_sum / n,
        "not_found": sum(1 for p in predictions if p["rank"] == 0),
    }


def by_difficulty(predictions: list[dict]) -> dict:
    by_diff: dict = defaultdict(list)
    for p in predictions:
        by_diff[p["difficulty"]].append(p)
    return {diff: compute_metrics(plist) for diff, plist in by_diff.items()}


def by_category(predictions: list[dict]) -> dict:
    by_cat: dict = defaultdict(list)
    for p in predictions:
        by_cat[p["category"]].append(p)
    return {cat: compute_metrics(plist) for cat, plist in by_cat.items()}


# ── 실험 실행 ─────────────────────────────────────────────────────────


def run_baseline(corpus, queries, embed_fn) -> dict:
    from scripts.linear_rag_experiment.baseline_vector_rag import BaselineVectorRAG

    print("\n=== Baseline Vector RAG ===")
    rag = BaselineVectorRAG(embed_fn=embed_fn)

    t0 = time.perf_counter()
    rag.index(corpus)
    index_time = time.perf_counter() - t0
    print(f"Indexing: {index_time:.3f}s")

    predictions = []
    latencies = []
    for q in queries:
        t1 = time.perf_counter()
        results = rag.retrieve(q["query"], top_k=5)
        latency = time.perf_counter() - t1
        latencies.append(latency)
        top_pids = [r.pid for r in results]
        rank = 0
        for i, pid in enumerate(top_pids, start=1):
            if pid == q["expected_pid"]:
                rank = i
                break
        predictions.append(
            {
                "qid": q["qid"],
                "query": q["query"],
                "expected_pid": q["expected_pid"],
                "top_pids": top_pids,
                "top_scores": [r.score for r in results],
                "rank": rank,
                "category": q["category"],
                "difficulty": q["difficulty"],
                "latency_s": latency,
            }
        )

    return {
        "system": "baseline_vector",
        "index_time_s": index_time,
        "predictions": predictions,
        "latencies_s": latencies,
        "metrics": compute_metrics(predictions),
        "metrics_by_difficulty": by_difficulty(predictions),
        "metrics_by_category": by_category(predictions),
    }


def run_linear_rag(corpus, queries, embed_fn, tenant_root: Path) -> dict:
    from v2.rag.linear_rag import LinearRAG, build_index, kms_table_to_corpus

    print("\n=== LinearRAG (Clean-Room ICLR'26) ===")
    items = kms_table_to_corpus(corpus)

    t0 = time.perf_counter()
    build = build_index(
        tenant_id="experiment",
        corpus=items,
        tenant_root=tenant_root,
        embed_fn=embed_fn,
    )
    index_time = time.perf_counter() - t0
    print(f"Indexing: {index_time:.3f}s")
    print(f"Tri-Graph: passages={build.graph_stats['num_passages']}, "
          f"sentences={build.graph_stats['num_sentences']}, "
          f"entities={build.graph_stats['num_entities']}, "
          f"C_nnz={build.graph_stats['C_nnz']}, M_nnz={build.graph_stats['M_nnz']}")

    rag = LinearRAG(tenant_id="experiment", tenant_root=tenant_root, embed_fn=embed_fn)

    predictions = []
    latencies = []
    for q in queries:
        t1 = time.perf_counter()
        result = rag.retrieve(q["query"], top_k=5)
        latency = time.perf_counter() - t1
        latencies.append(latency)
        top_pids = [p.pid for p in result.passages]
        rank = 0
        for i, pid in enumerate(top_pids, start=1):
            if pid == q["expected_pid"]:
                rank = i
                break
        predictions.append(
            {
                "qid": q["qid"],
                "query": q["query"],
                "expected_pid": q["expected_pid"],
                "top_pids": top_pids,
                "top_scores": [p.ppr_score for p in result.passages],
                "rank": rank,
                "category": q["category"],
                "difficulty": q["difficulty"],
                "latency_s": latency,
                "activated_entity_count": len(result.activated_entities),
            }
        )

    return {
        "system": "linear_rag",
        "index_time_s": index_time,
        "graph_stats": build.graph_stats,
        "predictions": predictions,
        "latencies_s": latencies,
        "metrics": compute_metrics(predictions),
        "metrics_by_difficulty": by_difficulty(predictions),
        "metrics_by_category": by_category(predictions),
    }


# ── 리포트 출력 ───────────────────────────────────────────────────────


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = int(len(s) * p / 100)
    return s[min(k, len(s) - 1)]


def print_report(results: list[dict]) -> None:
    print("\n" + "=" * 80)
    print("📊 LinearRAG vs Baseline 비교 리포트")
    print("=" * 80)

    # 종합 metric 표
    print("\n## Overall Metrics")
    header = f"{'System':<22}{'Top-1':>10}{'Top-3':>10}{'Top-5':>10}{'MRR':>10}{'Index(s)':>10}{'p50 ms':>10}{'p95 ms':>10}"
    print(header)
    print("-" * len(header))
    for r in results:
        m = r["metrics"]
        lat_ms = [x * 1000 for x in r["latencies_s"]]
        p50 = percentile(lat_ms, 50)
        p95 = percentile(lat_ms, 95)
        print(
            f"{r['system']:<22}"
            f"{m['top1_accuracy']:>10.1%}"
            f"{m['top3_accuracy']:>10.1%}"
            f"{m['top5_accuracy']:>10.1%}"
            f"{m['mrr']:>10.3f}"
            f"{r['index_time_s']:>10.2f}"
            f"{p50:>10.1f}"
            f"{p95:>10.1f}"
        )

    # 난이도별
    print("\n## By Difficulty")
    diffs = ["easy", "medium", "hard"]
    print(f"{'System':<22}", end="")
    for d in diffs:
        print(f"{'top1-' + d:>14}", end="")
    print()
    print("-" * 80)
    for r in results:
        print(f"{r['system']:<22}", end="")
        for d in diffs:
            mb = r["metrics_by_difficulty"].get(d, {})
            top1 = mb.get("top1_accuracy", 0.0)
            n = mb.get("n", 0)
            print(f"{f'{top1:.0%} ({n})':>14}", end="")
        print()

    # 차이점 분석 (LinearRAG 만 맞춘 / Baseline 만 맞춘 / 둘 다 틀린)
    if len(results) >= 2:
        baseline = next((r for r in results if r["system"] == "baseline_vector"), None)
        linear = next((r for r in results if r["system"] == "linear_rag"), None)
        if baseline and linear:
            base_correct = {p["qid"] for p in baseline["predictions"] if p["rank"] == 1}
            lin_correct = {p["qid"] for p in linear["predictions"] if p["rank"] == 1}
            both = base_correct & lin_correct
            only_lin = lin_correct - base_correct
            only_base = base_correct - lin_correct
            neither = (
                {p["qid"] for p in baseline["predictions"]}
                - base_correct - lin_correct
            )
            print("\n## Per-Query Analysis (Top-1 기준)")
            print(f"  둘 다 정답:        {len(both)}건  {sorted(both)}")
            print(f"  LinearRAG 만 정답: {len(only_lin)}건  {sorted(only_lin)}")
            print(f"  Baseline 만 정답:  {len(only_base)}건  {sorted(only_base)}")
            print(f"  둘 다 오답:        {len(neither)}건  {sorted(neither)}")

            if only_lin:
                print(f"\n## LinearRAG 만 맞춘 케이스 ({len(only_lin)}건)")
                for qid in sorted(only_lin):
                    bp = next(p for p in baseline["predictions"] if p["qid"] == qid)
                    lp = next(p for p in linear["predictions"] if p["qid"] == qid)
                    print(f"  [{qid}] {bp['query']}")
                    print(f"    expected: {bp['expected_pid']}")
                    print(f"    Baseline top-1: {bp['top_pids'][0] if bp['top_pids'] else '(none)'}")
                    print(f"    LinearRAG top-1: {lp['top_pids'][0] if lp['top_pids'] else '(none)'}")

            if only_base:
                print(f"\n## Baseline 만 맞춘 케이스 ({len(only_base)}건)")
                for qid in sorted(only_base):
                    bp = next(p for p in baseline["predictions"] if p["qid"] == qid)
                    lp = next(p for p in linear["predictions"] if p["qid"] == qid)
                    print(f"  [{qid}] {bp['query']}")
                    print(f"    expected: {bp['expected_pid']}")
                    print(f"    Baseline top-1: {bp['top_pids'][0] if bp['top_pids'] else '(none)'}")
                    print(f"    LinearRAG top-1: {lp['top_pids'][0] if lp['top_pids'] else '(none)'}")
                    print(f"    LinearRAG top-3: {lp['top_pids'][:3]}")


def main() -> int:
    output_dir = Path(os.environ.get("EXP_OUTPUT_DIR", DEFAULT_OUTPUT_DIR))
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading data...")
    corpus, queries = load_data()
    print(f"Corpus: {len(corpus)} KMS rows | Queries: {len(queries)}")

    print("Initializing Bedrock Titan v2 embedding...")
    embed_fn, cache = get_embed_fn()
    # warmup + sanity check
    if embed_fn("warmup") is None:
        print("ERROR: Bedrock embed 실패 — AWS 자격증명/리전 확인")
        return 1
    print(f"Embedding cache initialized")

    # Baseline first
    baseline_result = run_baseline(corpus, queries, embed_fn)

    # LinearRAG
    tenant_root = Path("/tmp/linear_rag_experiment")
    if tenant_root.exists():
        import shutil
        shutil.rmtree(tenant_root)
    tenant_root.mkdir(parents=True, exist_ok=True)
    linear_result = run_linear_rag(corpus, queries, embed_fn, tenant_root)

    results = [baseline_result, linear_result]
    print_report(results)

    # 저장
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_file = output_dir / f"experiment_{timestamp}.json"
    out_file.write_text(
        json.dumps(
            {
                "timestamp": timestamp,
                "n_corpus": len(corpus),
                "n_queries": len(queries),
                "embed_cache_size": len(cache),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n결과 저장: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
