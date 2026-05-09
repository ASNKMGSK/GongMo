# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""λ (DPR weight) sweep — passage initial 점수에서 DPR 유사도와 entity 기여도 trade-off."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from run_experiment import (
    DATA_DIR, get_embed_fn, compute_metrics, by_difficulty
)


def main():
    corpus = json.loads((DATA_DIR / "kms_corpus.json").read_text(encoding="utf-8"))
    queries = json.loads((DATA_DIR / "test_queries.json").read_text(encoding="utf-8"))
    embed_fn, _ = get_embed_fn()

    from v2.rag.linear_rag import (
        LinearRAG, LinearRAGConfig, build_index, kms_table_to_corpus
    )

    tenant_root = Path("/tmp/linear_rag_lambda_sweep")
    if tenant_root.exists():
        shutil.rmtree(tenant_root)
    tenant_root.mkdir(parents=True, exist_ok=True)

    items = kms_table_to_corpus(corpus)
    print("Indexing once...")
    build_index(
        tenant_id="sweep", corpus=items, tenant_root=tenant_root, embed_fn=embed_fn
    )

    # λ sweep
    sweep = [
        ("default 0.05", 0.05, 0.4),
        ("balanced 0.5", 0.5, 0.4),
        ("DPR-heavy 1.0", 1.0, 0.4),
        ("DPR-dominant 2.0", 2.0, 0.4),
        ("loose threshold 0.5/0.2", 0.5, 0.2),
        ("tight threshold 0.5/0.6", 0.5, 0.6),
    ]

    print(f"\n{'config':<28}{'top1':>8}{'top3':>8}{'mrr':>8}")
    print("-" * 60)
    rows = []
    for label, lam, threshold in sweep:
        cfg = LinearRAGConfig(lambda_coef=lam, threshold_delta=threshold)
        rag = LinearRAG(
            tenant_id="sweep", tenant_root=tenant_root,
            embed_fn=embed_fn, config=cfg
        )
        predictions = []
        for q in queries:
            res = rag.retrieve(q["query"], top_k=5)
            top_pids = [p.pid for p in res.passages]
            rank = 0
            for i, pid in enumerate(top_pids, start=1):
                if pid == q["expected_pid"]:
                    rank = i
                    break
            predictions.append({
                "qid": q["qid"], "expected_pid": q["expected_pid"],
                "top_pids": top_pids, "rank": rank,
                "category": q["category"], "difficulty": q["difficulty"],
            })
        m = compute_metrics(predictions)
        print(f"{label:<28}{m['top1_accuracy']:>8.1%}{m['top3_accuracy']:>8.1%}{m['mrr']:>8.3f}")
        rows.append({"config": label, "lambda": lam, "threshold": threshold, "metrics": m})

    out = SCRIPT_DIR / "results" / f"lambda_sweep_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
