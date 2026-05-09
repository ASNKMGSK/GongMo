# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
(Q, A) 페어 단위 KMS 검색 + 답변 평가 prototype.

흐름:
  transcript
    → 1. turn 분리 (고객/상담사)
    → 2. (q, a) 페어링 (인접 고객→상담사)
    → 3. LLM Router (q + KMS 18행 표 → 매칭 pid)
    → 4. KMS lookup
    → 5. LLM Judge (a 가 required_statements 충족했나)
    → 6. 종합 점수

KMS 18 행 작은 환경에서 RAG 우회. LLM 가 직접 18행 표 보고 분류.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import boto3

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"
RESULTS_DIR = SCRIPT_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

KMS_PATH = DATA_DIR / "kolon_kms.json"
CASES_PATH = DATA_DIR / "kolon_test_cases.json"

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")

BEDROCK = boto3.client("bedrock-runtime", region_name=REGION)


# ── 1. Turn 분리 ──────────────────────────────────────────────────────


SPEAKER_LINE_RE = re.compile(r"^(고객|상담사)\s*:\s*(.*)$")


def split_turns(transcript: str) -> list[dict]:
    """transcript 텍스트 → [{speaker, text}, ...]. 빈 turn 자동 제거.

    한 speaker 의 발화가 여러 줄로 split 된 경우 다음 speaker 라인 직전까지 합침.
    """
    turns: list[dict] = []
    current_speaker: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        if current_speaker and current_lines:
            text = " ".join(current_lines).strip()
            if text:
                turns.append({"speaker": current_speaker, "text": text})

    for raw_line in transcript.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = SPEAKER_LINE_RE.match(line)
        if m:
            _flush()
            current_speaker = m.group(1)
            content = m.group(2).strip()
            current_lines = [content] if content else []
        else:
            if current_speaker:
                current_lines.append(line)
    _flush()
    return turns


def make_qa_pairs(turns: list[dict], min_q_len: int = 8) -> list[tuple[str, str]]:
    """인접 (고객 turn → 상담사 turn) 페어. 짧은 추임새 (예/네/아) 필터.

    같은 고객 발화 연속 시 마지막 직전까지 합쳐서 다음 상담사 답변과 페어링.
    """
    pairs: list[tuple[str, str]] = []
    i = 0
    n = len(turns)
    while i < n:
        if turns[i]["speaker"] == "고객":
            # 연속 고객 발화 합치기
            customer_buf = [turns[i]["text"]]
            j = i + 1
            while j < n and turns[j]["speaker"] == "고객":
                customer_buf.append(turns[j]["text"])
                j += 1
            q = " ".join(customer_buf).strip()

            # 다음 상담사 발화 (연속) 합치기
            if j < n and turns[j]["speaker"] == "상담사":
                agent_buf = []
                k = j
                while k < n and turns[k]["speaker"] == "상담사":
                    agent_buf.append(turns[k]["text"])
                    k += 1
                a = " ".join(agent_buf).strip()
                if len(q) >= min_q_len and a:
                    pairs.append((q, a))
                i = k
                continue
        i += 1
    return pairs


# ── 2. LLM Router & Judge ─────────────────────────────────────────────


_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _bedrock_json(prompt: str, max_tokens: int = 600) -> dict:
    """Bedrock Sonnet 4 호출 후 첫 JSON 객체 반환. 실패시 빈 dict."""
    resp = BEDROCK.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.0},
    )
    text = resp["output"]["message"]["content"][0]["text"]
    # 가장 큰 JSON 블록 추출
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {"_raw": text}
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return {"_raw": text}


def llm_router(q: str, kms_rows: list[dict]) -> dict:
    table_lines = ["| pid | intent | branch | condition | keywords |", "|---|---|---|---|---|"]
    for r in kms_rows:
        cond = (r.get("condition") or "")[:80].replace("|", " ")
        kws = ", ".join((r.get("required_keywords") or [])[:6])
        table_lines.append(f"| {r['pid']} | {r['intent']} | {r['branch']} | {cond} | {kws} |")
    table_md = "\n".join(table_lines)

    prompt = f"""다음 KMS 표에서 고객 질문에 가장 부합하는 행을 1개 골라 pid 를 반환.

KMS 표:
{table_md}

고객 질문: {q}

규칙:
- pid 는 위 표의 pid 컬럼 값 그대로.
- 매칭되는 행이 없으면 pid="none".
- intent 만 보지 말고 branch (분기) 까지 정확히 식별. 예: 환불 → 카드/계좌 분기.

JSON 만 출력 (다른 텍스트 금지):
{{"pid": "...", "reason": "한 문장 근거"}}"""
    return _bedrock_json(prompt, max_tokens=200)


def llm_judge(q: str, a: str, kms_row: dict) -> dict:
    statements = kms_row.get("required_statements") or []
    if not statements:
        return {"score": None, "satisfied": [], "missing": [], "note": "no required statements"}

    stmt_md = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(statements))

    prompt = f"""상담사가 KMS 의 필수 안내사항을 의미상 전달했는지 평가.

[고객 질문]
{q}

[상담사 답변]
{a}

[KMS 분기: {kms_row.get("intent")} - {kms_row.get("branch")}]
필수 안내 항목:
{stmt_md}

평가 규칙:
- 표현이 달라도 의미상 동일하게 안내했으면 satisfied.
- 누락되었거나 부정확하게 안내했으면 missing.
- evidence 는 답변 원문에서 발췌한 짧은 문구.

JSON 만 출력:
{{"satisfied": [1,3], "missing": [2,4], "evidence": {{"1": "답변 발췌...", "3": "..."}}}}"""
    return _bedrock_json(prompt, max_tokens=800)


# ── 3. 종합 ───────────────────────────────────────────────────────────


def evaluate_transcript(transcript: str, kms_rows: list[dict], max_pairs: int | None = None) -> dict:
    turns = split_turns(transcript)
    pairs = make_qa_pairs(turns)
    if max_pairs:
        pairs = pairs[:max_pairs]

    kms_by_pid = {r["pid"]: r for r in kms_rows}

    pair_results: list[dict] = []
    for idx, (q, a) in enumerate(pairs):
        t0 = time.time()
        router = llm_router(q, kms_rows)
        pid = (router or {}).get("pid")
        kms_row = kms_by_pid.get(pid) if pid else None

        judgment: dict | None = None
        if kms_row and not kms_row.get("is_evaluation_skip"):
            judgment = llm_judge(q, a, kms_row)

        pair_results.append({
            "idx": idx,
            "q": q[:160],
            "a": a[:240],
            "router": router,
            "kms_pid": pid,
            "kms_intent": kms_row["intent"] if kms_row else None,
            "kms_branch": kms_row["branch"] if kms_row else None,
            "judgment": judgment,
            "elapsed_s": round(time.time() - t0, 2),
        })

    matched = sum(1 for p in pair_results if p["kms_pid"] and p["kms_pid"] != "none")
    judged = [p for p in pair_results if p["judgment"] and isinstance(p["judgment"].get("satisfied"), list)]
    total_required = sum(len((p["judgment"].get("satisfied") or [])) + len((p["judgment"].get("missing") or [])) for p in judged)
    total_satisfied = sum(len(p["judgment"].get("satisfied") or []) for p in judged)
    statement_pass_rate = (total_satisfied / total_required) if total_required else None

    return {
        "n_turns": len(turns),
        "n_pairs": len(pairs),
        "n_kms_matched": matched,
        "n_judged": len(judged),
        "statement_pass_rate": statement_pass_rate,
        "pairs": pair_results,
    }


# ── main ──────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="case_id 1개 (지정 시 해당 케이스만)", default=None)
    ap.add_argument("--max-pairs", type=int, default=8, help="페어 수 제한 (비용 절감)")
    ap.add_argument("--limit", type=int, default=1, help="처리할 케이스 수")
    args = ap.parse_args()

    kms = json.loads(KMS_PATH.read_text(encoding="utf-8"))
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))

    if args.case:
        cases = [c for c in cases if str(c["case_id"]) == args.case]
    cases = cases[: args.limit]

    print(f"KMS rows: {len(kms)}")
    print(f"Cases to process: {len(cases)}")
    print(f"Model: {MODEL_ID}")
    print()

    out = []
    for i, c in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] case={c['case_id']} | {c.get('description', '')[:80]}")
        result = evaluate_transcript(c.get("transcript", ""), kms, max_pairs=args.max_pairs)
        out.append({
            "case_id": c["case_id"],
            "filename": c.get("filename"),
            "gt_intents": c.get("gt_intents"),
            "split": c.get("split"),
            **result,
        })
        print(f"  turns={result['n_turns']} pairs={result['n_pairs']} matched={result['n_kms_matched']} judged={result['n_judged']} pass_rate={result['statement_pass_rate']}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS_DIR / f"per_pair_eval_{ts}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
