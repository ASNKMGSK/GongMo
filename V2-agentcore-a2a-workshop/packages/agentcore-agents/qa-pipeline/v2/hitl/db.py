# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""HITL SQLite 스토리지 — human_reviews + golden_set_candidates.

프론트 수정 모드에서 전송한 human_score/note 를 저장하고, 튜닝 우선순위
집계 및 golden-set 후보 승인 플로우를 지원한다.

DB 경로는 환경변수 ``QA_HITL_DB_PATH`` 로 override 가능. 미지정 시
``~/Desktop/QA평가결과/human_reviews.db`` 를 사용한다 (기존 /save-xlsx
저장 루트와 동일 계열).

`init_db()` 는 모듈 import 시 자동 호출하지 않는다. 첫 endpoint 호출 시
명시 호출 (server_v2 에서 lazy init).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_DEFAULT_DB_SUBPATH = Path.home() / "Desktop" / "QA평가결과" / "human_reviews.db"


def _db_path() -> Path:
    override = os.environ.get("QA_HITL_DB_PATH")
    return Path(override) if override else _DEFAULT_DB_SUBPATH


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    """SQLite connection (row_factory=Row). 부모 폴더는 on-demand 생성."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


_SCHEMA_HUMAN_REVIEWS = """
CREATE TABLE IF NOT EXISTS human_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    consultation_id TEXT NOT NULL,
    item_number INTEGER NOT NULL,
    ai_score REAL,
    human_score REAL,
    ai_evidence TEXT,
    ai_judgment TEXT,
    human_note TEXT,
    ai_confidence REAL,
    reviewer_id TEXT,
    reviewer_role TEXT DEFAULT 'senior',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    force_t3 INTEGER DEFAULT 0,
    site_id TEXT,
    channel TEXT,
    department TEXT
)
"""

_SCHEMA_HUMAN_REVIEWS_UNIQUE = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_human_reviews_consult_item
ON human_reviews (consultation_id, item_number)
"""

_SCHEMA_HUMAN_REVIEWS_STATUS = """
CREATE INDEX IF NOT EXISTS idx_human_reviews_status
ON human_reviews (status)
"""

_SCHEMA_HUMAN_REVIEWS_TENANT3 = """
CREATE INDEX IF NOT EXISTS idx_human_reviews_tenant3
ON human_reviews (site_id, channel, department)
"""

_SCHEMA_GOLDEN_CANDIDATES = """
CREATE TABLE IF NOT EXISTS golden_set_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL,
    consultation_id TEXT,
    item_number INTEGER,
    transcript_excerpt TEXT,
    human_score REAL,
    human_note TEXT,
    delta REAL,
    ai_confidence REAL,
    status TEXT DEFAULT 'pending_approval',
    approved_by TEXT,
    approved_at TEXT,
    indexed_at TEXT,
    created_at TEXT NOT NULL,
    site_id TEXT,
    channel TEXT,
    department TEXT,
    FOREIGN KEY (review_id) REFERENCES human_reviews(id)
)
"""

_SCHEMA_GOLDEN_STATUS = """
CREATE INDEX IF NOT EXISTS idx_golden_candidates_status
ON golden_set_candidates (status)
"""

_SCHEMA_GOLDEN_TENANT3 = """
CREATE INDEX IF NOT EXISTS idx_golden_candidates_tenant3
ON golden_set_candidates (site_id, channel, department)
"""


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, col_def: str
) -> None:
    """SQLite 가 ADD COLUMN IF NOT EXISTS 미지원 → PRAGMA 로 존재 체크 후 ALTER.

    기존 배포에서 생성된 구 스키마 테이블에 3단계 멀티테넌트 컬럼을 안전하게 추가.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")


def init_db() -> None:
    """테이블/인덱스 생성 (CREATE IF NOT EXISTS) + 3단계 멀티테넌트 마이그레이션."""
    with get_conn() as conn:
        conn.execute(_SCHEMA_HUMAN_REVIEWS)
        conn.execute(_SCHEMA_HUMAN_REVIEWS_UNIQUE)
        conn.execute(_SCHEMA_HUMAN_REVIEWS_STATUS)
        conn.execute(_SCHEMA_GOLDEN_CANDIDATES)
        conn.execute(_SCHEMA_GOLDEN_STATUS)

        # 구 스키마 레코드 호환 — ALTER TABLE 로 site_id / channel / department 추가.
        # 기존 행은 NULL 로 유지되며 필요 시 백필 스크립트로 채울 수 있다.
        for table in ("human_reviews", "golden_set_candidates"):
            _ensure_column(conn, table, "site_id", "TEXT")
            _ensure_column(conn, table, "channel", "TEXT")
            _ensure_column(conn, table, "department", "TEXT")

        conn.execute(_SCHEMA_HUMAN_REVIEWS_TENANT3)
        conn.execute(_SCHEMA_GOLDEN_TENANT3)


def _serialize_evidence(evidence: Any) -> str | None:
    if evidence is None:
        return None
    if isinstance(evidence, str):
        return evidence
    try:
        return json.dumps(evidence, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(evidence)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    evidence = d.get("ai_evidence")
    if isinstance(evidence, str) and evidence:
        try:
            d["ai_evidence"] = json.loads(evidence)
        except (TypeError, ValueError):
            pass
    return d


def upsert_review(
    *,
    consultation_id: str,
    item_number: int,
    ai_score: float | None = None,
    human_score: float | None = None,
    ai_evidence: Any = None,
    ai_judgment: str | None = None,
    human_note: str | None = None,
    ai_confidence: float | None = None,
    reviewer_id: str | None = None,
    reviewer_role: str | None = None,
    force_t3: bool | int | None = None,
    status: str | None = None,
    site_id: str | None = None,
    channel: str | None = None,
    department: str | None = None,
) -> int:
    """(consultation_id, item_number) 기준 INSERT OR REPLACE. 생성된 row id 반환.

    기존 row 가 있을 경우 id 가 바뀌므로 UNIQUE 제약 기반 UPSERT (ON CONFLICT)
    를 사용. created_at 은 최초 생성 시에만 세팅.

    3단계 멀티테넌트 (2026-04-24): site_id/channel/department 선택 컬럼 저장.
    """
    evidence_str = _serialize_evidence(ai_evidence)
    force_flag = 1 if (force_t3 is True or force_t3 == 1) else 0
    status_val = status or "pending"
    role_val = reviewer_role or "senior"
    now = _now_iso()
    sql = """
    INSERT INTO human_reviews (
        consultation_id, item_number, ai_score, human_score, ai_evidence, ai_judgment,
        human_note, ai_confidence, reviewer_id, reviewer_role, status, created_at, force_t3,
        site_id, channel, department
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (consultation_id, item_number) DO UPDATE SET
        ai_score = excluded.ai_score,
        human_score = excluded.human_score,
        ai_evidence = excluded.ai_evidence,
        ai_judgment = excluded.ai_judgment,
        human_note = excluded.human_note,
        ai_confidence = excluded.ai_confidence,
        reviewer_id = excluded.reviewer_id,
        reviewer_role = excluded.reviewer_role,
        status = excluded.status,
        force_t3 = excluded.force_t3,
        site_id = COALESCE(excluded.site_id, human_reviews.site_id),
        channel = COALESCE(excluded.channel, human_reviews.channel),
        department = COALESCE(excluded.department, human_reviews.department)
    """
    with get_conn() as conn:
        conn.execute(
            sql,
            (
                consultation_id,
                int(item_number),
                ai_score,
                human_score,
                evidence_str,
                ai_judgment,
                human_note,
                ai_confidence,
                reviewer_id,
                role_val,
                status_val,
                now,
                force_flag,
                site_id,
                channel,
                department,
            ),
        )
        cur = conn.execute(
            "SELECT id FROM human_reviews WHERE consultation_id = ? AND item_number = ?",
            (consultation_id, int(item_number)),
        )
        row = cur.fetchone()
        return int(row["id"]) if row else 0


def list_reviews(
    status: str | None = None,
    force_t3_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM human_reviews"
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if force_t3_only:
        clauses.append("force_t3 = 1")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [d for d in (_row_to_dict(r) for r in rows) if d is not None]


def get_review(review_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM human_reviews WHERE id = ?", (int(review_id),)
        ).fetchone()
        return _row_to_dict(row)


def confirm_review(
    review_id: int,
    reviewer_id: str,
    reviewer_role: str | None = None,
) -> bool:
    """status='confirmed', confirmed_at=now. 존재하지 않으면 False."""
    now = _now_iso()
    role_val = reviewer_role or "senior"
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE human_reviews
               SET status = 'confirmed',
                   confirmed_at = ?,
                   reviewer_id = ?,
                   reviewer_role = ?
             WHERE id = ?
            """,
            (now, reviewer_id, role_val, int(review_id)),
        )
        return cur.rowcount > 0


def insert_golden_candidate(
    *,
    review_id: int,
    consultation_id: str | None,
    item_number: int | None,
    transcript_excerpt: str | None,
    human_score: float | None,
    human_note: str | None,
    delta: float | None,
    ai_confidence: float | None,
    status: str = "pending_approval",
) -> int:
    now = _now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO golden_set_candidates (
                review_id, consultation_id, item_number, transcript_excerpt,
                human_score, human_note, delta, ai_confidence, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(review_id),
                consultation_id,
                int(item_number) if item_number is not None else None,
                transcript_excerpt,
                human_score,
                human_note,
                delta,
                ai_confidence,
                status,
                now,
            ),
        )
        return int(cur.lastrowid or 0)


def list_candidates(
    status: str = "pending_approval",
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM golden_set_candidates"
    params: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(int(limit))
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def approve_candidate(candidate_id: int, approved_by: str) -> bool:
    now = _now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE golden_set_candidates
               SET status = 'approved', approved_by = ?, approved_at = ?
             WHERE id = ?
            """,
            (approved_by, now, int(candidate_id)),
        )
        return cur.rowcount > 0


def aggregate_priorities(rolling_n: int = 50) -> list[dict[str, Any]]:
    """항목별 confirmed 리뷰 기반 rolling-N 통계.

    각 item_number 에 대해 최근 N 건 confirmed 리뷰의 MAE / Bias / override_pct
    를 산출. sample_count < N 이면 metric 들은 None 으로 반환 (프론트에서 '수집중'
    표기 용). override 는 abs(ai - human) > 0 인 건.
    """
    with get_conn() as conn:
        items_rows = conn.execute(
            "SELECT DISTINCT item_number FROM human_reviews ORDER BY item_number"
        ).fetchall()
        item_numbers = [int(r["item_number"]) for r in items_rows]

        result: list[dict[str, Any]] = []
        for item in item_numbers:
            rows = conn.execute(
                """
                SELECT ai_score, human_score
                  FROM human_reviews
                 WHERE item_number = ?
                   AND status = 'confirmed'
                   AND ai_score IS NOT NULL
                   AND human_score IS NOT NULL
                 ORDER BY COALESCE(confirmed_at, created_at) DESC
                 LIMIT ?
                """,
                (item, int(rolling_n)),
            ).fetchall()

            n = len(rows)
            if n < rolling_n:
                result.append(
                    {
                        "item_number": item,
                        "mae": None,
                        "bias": None,
                        "override_pct": None,
                        "sample_count": n,
                    }
                )
                continue

            diffs = [float(r["ai_score"]) - float(r["human_score"]) for r in rows]
            abs_diffs = [abs(d) for d in diffs]
            mae = sum(abs_diffs) / n
            bias = sum(diffs) / n
            overrides = sum(1 for d in abs_diffs if d > 0)
            override_pct = (overrides / n) * 100.0

            result.append(
                {
                    "item_number": item,
                    "mae": round(mae, 4),
                    "bias": round(bias, 4),
                    "override_pct": round(override_pct, 2),
                    "sample_count": n,
                }
            )

        result.sort(
            key=lambda x: (x["mae"] if x["mae"] is not None else -1),
            reverse=True,
        )
        return result
