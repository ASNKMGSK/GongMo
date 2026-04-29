# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""HITL Markdown Exporter — 검수 확정 시 (transcript_excerpt + AI 판정 + 사람 정답) 트리플렛을
폴더에 MD 파일로 적재.

설계:
- 저장 위치: ``~/Desktop/QA평가결과/HITL_RAG/`` (env ``QA_HITL_RAG_ROOT`` override).
- 파일명:  ``{cid}_{item_number:02d}.md`` — confirm 호출마다 동일 cid/item 은 덮어쓰기.
- frontmatter (yaml-like) + body 의 단순 마크다운. 외부 라이브러리 의존 X.
- transcript_excerpt 는 review row 의 ``ai_evidence`` (parsed turn 들) 에서만 추출 — 사용자 정책
  ("파싱된 부분에만 대해서만 하면 될거 같고").
- ``human_score`` 미입력 (None) 인 row 는 export 안 함 — RAG 학습 자료로 부적합.
- 동일 (cid, item_no) 에 대해 ``human_score`` 가 변경된 경우에만 ``indexed_at`` 헤더 비움 →
  rag_ingester 가 변경 감지해 재임베딩. 점수 동일 시 mtime 만 갱신 (재임베딩 X).

호출 시점: ``server_v2._save_consultation_edits_snapshot`` 직후. background task 로 비동기 실행.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


_DEFAULT_ROOT = Path.home() / "Desktop" / "QA평가결과" / "HITL_RAG"


def resolve_rag_root() -> Path:
    """HITL RAG MD 저장 루트. env ``QA_HITL_RAG_ROOT`` 우선, 기본 위 _DEFAULT_ROOT."""
    override = os.environ.get("QA_HITL_RAG_ROOT")
    return Path(override) if override else _DEFAULT_ROOT


def _safe_cid(cid: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", str(cid or "unknown"))
    return safe or "unknown"


def md_path_for(cid: str, item_number: int) -> Path:
    return resolve_rag_root() / f"{_safe_cid(cid)}_{int(item_number):02d}.md"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# transcript_excerpt 추출 — ai_evidence 의 quote 들만 (파싱된 부분)
# ---------------------------------------------------------------------------


def _coerce_evidence_list(raw: Any) -> list[dict[str, Any]]:
    """ai_evidence 가 JSON 문자열 / list / dict 어떤 형태로 와도 list[dict] 로 정규화.

    list 안에 string 만 있는 케이스 (예: HITL 시드/upsert 직접 입력 — `["발화1", "발화2"]`) 도
    `{"speaker": "발화자", "quote": "..."}` 으로 보강. 노드에서 전달된 dict evidence 는 그대로 유지.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            import json
            parsed = json.loads(s)
            return _coerce_evidence_list(parsed)
        except Exception:
            # JSON 이 아니면 단일 발화 문자열로 취급
            return [{"speaker": "발화자", "quote": s}]
    if isinstance(raw, dict):
        # {evidence: [...]} 형태일 수 있음
        if "evidence" in raw and isinstance(raw["evidence"], list):
            return _coerce_evidence_list(raw["evidence"])
        return [raw]
    if isinstance(raw, list):
        out: list[dict[str, Any]] = []
        for ev in raw:
            if isinstance(ev, dict):
                out.append(ev)
            elif isinstance(ev, str):
                text = ev.strip()
                if text:
                    out.append({"speaker": "발화자", "quote": text})
        return out
    return []


def build_transcript_excerpt(evidence: Any) -> str:
    """ai_evidence → 사람이 읽을 수 있는 발화 슬라이스.

    형식: ``{speaker}: "{quote}"`` 한 줄씩, turn_id 오름차순.
    """
    items = _coerce_evidence_list(evidence)
    if not items:
        return ""
    # turn_id 가 있는 것만 정렬, 없는 건 입력 순서 유지.
    items_sorted = sorted(
        items,
        key=lambda e: (0, int(e["turn_id"])) if isinstance(e.get("turn_id"), (int, float)) else (1, 0),
    )
    lines: list[str] = []
    for ev in items_sorted:
        speaker = str(ev.get("speaker") or "").strip() or "발화자"
        quote = str(ev.get("quote") or ev.get("text") or "").strip()
        if not quote:
            continue
        # 인용 문자 정리 — 줄바꿈 제거
        quote_one = re.sub(r"\s+", " ", quote)
        lines.append(f"- {speaker}: \"{quote_one}\"")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown 직렬화 — frontmatter + body
# ---------------------------------------------------------------------------


def _yaml_escape(value: Any) -> str:
    """frontmatter scalar 직렬화 — 큰따옴표 escape, 줄바꿈 차단."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    s = s.replace("\\", "\\\\").replace("\"", "\\\"")
    s = s.replace("\n", " ").replace("\r", " ")
    return f"\"{s}\""


def _render_frontmatter(meta: dict[str, Any]) -> str:
    lines = ["---"]
    for k in (
        "consultation_id",
        "item_number",
        "item_name",
        "ai_score",
        "human_score",
        "max_score",
        "delta",
        "status",
        "reviewer_id",
        "reviewer_role",
        "confirmed_at",
        "site_id",
        "channel",
        "department",
        "indexed_at",
        "score_signature",
    ):
        if k in meta:
            lines.append(f"{k}: {_yaml_escape(meta.get(k))}")
    lines.append("---")
    return "\n".join(lines)


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> dict[str, Any]:
    """단순 frontmatter 파서 — `key: "value"` / `key: 123` / `key: null`.

    완전한 YAML 스펙은 따르지 않음. 본 모듈이 만든 파일만 다시 읽기 위함.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    body = m.group(1)
    out: dict[str, Any] = {}
    for line in body.splitlines():
        line = line.rstrip()
        if not line or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if raw == "null" or raw == "":
            out[key] = None
        elif raw == "true":
            out[key] = True
        elif raw == "false":
            out[key] = False
        elif raw.startswith("\"") and raw.endswith("\""):
            inner = raw[1:-1]
            inner = inner.replace("\\\"", "\"").replace("\\\\", "\\")
            out[key] = inner
        else:
            try:
                if "." in raw:
                    out[key] = float(raw)
                else:
                    out[key] = int(raw)
            except ValueError:
                out[key] = raw
    return out


def parse_md_file(path: Path) -> dict[str, Any]:
    """저장된 MD 파일 → {meta(dict), body(str)} 구조로 재로드 — ingester / retriever 공용."""
    text = path.read_text(encoding="utf-8")
    meta = parse_frontmatter(text)
    body = _FRONTMATTER_RE.sub("", text, count=1)
    return {"meta": meta, "body": body.strip()}


# ---------------------------------------------------------------------------
# Public API — exporter 의 단일 진입점
# ---------------------------------------------------------------------------


def _score_signature(ai_score: Any, human_score: Any) -> str:
    """human_score 변경 감지용. 두 점수 페어 hash 가 달라지면 재임베딩 트리거."""
    return f"ai={ai_score}|human={human_score}"


def export_review_row(row: dict[str, Any], *, item_meta: dict[str, Any] | None = None) -> Path | None:
    """단일 ``human_reviews`` 행 → MD 파일.

    Parameters
    ----------
    row : dict
        ``human_reviews`` 한 row (sqlite3.Row 를 dict 로 변환). 최소 필드:
        consultation_id, item_number, ai_score, human_score, ai_evidence, ai_judgment,
        human_note, status, confirmed_at, reviewer_id, reviewer_role, site_id,
        channel, department.
    item_meta : dict | None
        report.evaluation 에서 가져올 수 있는 item 메타. ``item_name``, ``max_score``
        가 있으면 frontmatter 에 포함.

    Returns
    -------
    Path | None
        저장된 파일 경로. ``human_score is None`` 또는 ``status != "confirmed"`` 면 None.
    """
    status = str(row.get("status") or "").strip()
    if status != "confirmed":
        return None
    human_score = row.get("human_score")
    if human_score is None:
        return None

    cid = str(row.get("consultation_id") or "").strip()
    item_no_raw = row.get("item_number")
    if not cid or item_no_raw is None:
        return None
    try:
        item_no = int(item_no_raw)
    except (TypeError, ValueError):
        return None

    ai_score = row.get("ai_score")
    ai_judgment = (row.get("ai_judgment") or "").strip() or "(AI 판정 사유 누락)"
    human_note = (row.get("human_note") or "").strip()
    transcript_excerpt = build_transcript_excerpt(row.get("ai_evidence"))

    item_meta = item_meta or {}
    item_name = str(item_meta.get("item_name") or "").strip()
    max_score = item_meta.get("max_score")

    # item_meta 누락 시 글로벌 매핑에서 fallback —
    # HITL 단독 시드 row (파이프라인 결과 JSON 없음) 도 배점/이름 표시.
    if not item_name:
        try:
            from v2.hitl.export import ITEM_NAMES as _ITEM_NAMES

            item_name = (_ITEM_NAMES.get(item_no) or "").strip()
        except Exception:
            pass
    if max_score is None:
        try:
            from v2.contracts.rubric import ALLOWED_STEPS as _ALLOWED

            steps = _ALLOWED.get(item_no)
            if steps:
                max_score = steps[0]
        except Exception:
            pass

    # delta 계산 (AI - 사람) — 음수면 사람이 더 후함, 양수면 더 엄격
    try:
        delta: float | None = float(ai_score) - float(human_score) if ai_score is not None else None
    except (TypeError, ValueError):
        delta = None

    sig = _score_signature(ai_score, human_score)
    target = md_path_for(cid, item_no)
    target.parent.mkdir(parents=True, exist_ok=True)

    # 변경 감지: 기존 파일의 score_signature 와 비교 → 동일이면 indexed_at 보존, 다르면 비움.
    prev_indexed_at: str | None = None
    if target.exists():
        try:
            prev = parse_md_file(target)
            prev_meta = prev.get("meta") or {}
            if prev_meta.get("score_signature") == sig:
                prev_indexed_at = prev_meta.get("indexed_at") or None
        except Exception as exc:
            logger.warning("기존 MD 파싱 실패 (%s) — 강제 재작성: %s", target.name, exc)

    meta_out: dict[str, Any] = {
        "consultation_id": cid,
        "item_number": item_no,
        "item_name": item_name or f"item_{item_no}",
        "ai_score": ai_score,
        "human_score": human_score,
        "max_score": max_score,
        "delta": delta,
        "status": status,
        "reviewer_id": row.get("reviewer_id"),
        "reviewer_role": row.get("reviewer_role"),
        "confirmed_at": row.get("confirmed_at"),
        "site_id": row.get("site_id"),
        "channel": row.get("channel"),
        "department": row.get("department"),
        "indexed_at": prev_indexed_at,  # None 이면 ingester 가 신규로 인식
        "score_signature": sig,
    }

    body_lines = [
        f"# HITL Review — #{item_no} {item_name}".rstrip(),
        "",
        "## 평가 항목",
        f"- 항목 #{item_no} ({item_name or '미지정'})  배점: {max_score if max_score is not None else '?'}",
        "",
        "## 발화 발췌 (AI evidence 파싱)",
        transcript_excerpt or "(파싱된 evidence 없음)",
        "",
        "## AI 판정",
        f"- 점수: {ai_score if ai_score is not None else '?'}",
        f"- 사유: {ai_judgment}",
        "",
        "## 사람 정답",
        f"- 점수: {human_score}",
        f"- 코멘트: {human_note or '(코멘트 없음)'}",
        f"- delta (AI - 사람): {delta if delta is not None else '?'}",
        "",
    ]
    body = "\n".join(body_lines)

    text = _render_frontmatter(meta_out) + "\n\n" + body
    target.write_text(text, encoding="utf-8")
    logger.info("hitl_md_exporter: wrote %s (signature=%s, prev_indexed=%s)", target.name, sig, prev_indexed_at)
    return target


def export_consultation_confirmed(
    consultation_id: str,
    *,
    review_rows: Iterable[dict[str, Any]],
    item_meta_by_number: dict[int, dict[str, Any]] | None = None,
) -> list[Path]:
    """주어진 review rows 중 ``status='confirmed'`` 만 MD 파일로 저장.

    ``item_meta_by_number`` 가 있으면 frontmatter 의 ``item_name`` / ``max_score`` 채움.
    실패한 row 는 warning 후 skip — 다른 row 는 계속 진행.
    """
    item_meta_by_number = item_meta_by_number or {}
    written: list[Path] = []
    for row in review_rows:
        try:
            item_no = int(row.get("item_number") or 0)
        except (TypeError, ValueError):
            continue
        try:
            p = export_review_row(row, item_meta=item_meta_by_number.get(item_no))
        except Exception as exc:
            logger.warning("export_review_row 실패 cid=%s item=%s — %s", consultation_id, item_no, exc)
            continue
        if p is not None:
            written.append(p)
    logger.info("hitl_md_exporter: cid=%s written=%d", consultation_id, len(written))
    return written
