# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
AOSS QA 벡터 인덱스 부트스트랩 — tenants/*/golden_set/*.json + reasoning_index/*.json
→ Titan Embed v2 → AOSS `qa-golden-set` / `qa-reasoning-index`.

실행:
    cd packages/agentcore-agents/qa-pipeline
    python v2/scripts/bootstrap_aoss_qa.py                      # 전체 tenant 색인
    python v2/scripts/bootstrap_aoss_qa.py --tenant kolon       # 특정 tenant 만
    python v2/scripts/bootstrap_aoss_qa.py --recreate            # 기존 인덱스 삭제 후 재생성
    python v2/scripts/bootstrap_aoss_qa.py --dry-run             # 임베딩만 계산, upload 안함

환경변수:
    QA_AOSS_ENDPOINT  : AOSS endpoint URL (없으면 SSM 에서 자동 조회)
    AWS_REGION        : default us-east-1

구현 특징:
- 임베딩 키: golden = segment_text + " | " + rationale
            reasoning = rationale + " | " + quote_example
  (rationale 단독보다 구체 발화 + 근거 합치면 검색 정확도 ↑)
- `stub_seed: true` 레코드도 색인 (dev 용).
- 실패 레코드는 건너뛰고 카운트만 리포트.
"""

from __future__ import annotations

from typing import Any

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path


def _content_hash(*parts: object) -> str:
    """문서 내용 기반 short hash (SHA1 앞 10자리).

    external_id 에 포함해 내용 변경 자동 감지. 같은 id/다른 hash → 새 doc 으로 색인.
    None/list/dict 도 JSON serialize 하여 일관성 유지.
    """
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]

_PIPELINE_DIR = Path(__file__).resolve().parents[2]  # qa-pipeline/
sys.path.insert(0, str(_PIPELINE_DIR))

from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402

from v2.rag.aoss_store import AossStore, GOLDEN_INDEX, REASONING_INDEX, KNOWLEDGE_INDEX  # noqa: E402
from v2.rag.business_knowledge import BusinessKnowledgeRAG  # noqa: E402
from v2.rag.embedding import embed, get_backend  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("bootstrap_aoss_qa")

# 병렬화 튜닝 기본값 — CLI --concurrency / --batch-size 로 오버라이드
DEFAULT_EMBED_CONCURRENCY = 32  # Titan Embed v2 동시 호출 수
DEFAULT_BULK_BATCH = 500  # _bulk 요청당 문서 수

_TENANTS_DIR = _PIPELINE_DIR / "v2" / "tenants"


def _list_tenants() -> list[str]:
    if not _TENANTS_DIR.exists():
        return []
    return sorted([d.name for d in _TENANTS_DIR.iterdir() if d.is_dir()])


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("%s JSON 파싱 실패: %s", path.name, e)
        return {}


def _embed_text(text: str) -> list[float] | None:
    vec = embed(text)
    return list(vec) if vec else None


def _emit_progress(tenant: str, kind: str, **kw) -> None:
    """프론트 파싱용 구조화된 progress 라인.

    형식: `PROGRESS tenant=<X> kind=<golden|reasoning|knowledge> key=value ...`
    프론트가 정규식으로 파싱하여 progress bar 갱신.
    """
    parts = [f"tenant={tenant}", f"kind={kind}"]
    for k, v in kw.items():
        parts.append(f"{k}={v}")
    print(f"PROGRESS {' '.join(parts)}", flush=True)


def _count_golden_total(tenant_id: str) -> int:
    dir_ = _TENANTS_DIR / tenant_id / "golden_set"
    if not dir_.exists():
        return 0
    n = 0
    for path in sorted(dir_.glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = _load_json(path)
        n += len(data.get("examples", []))
    return n


def _count_reasoning_total(tenant_id: str) -> int:
    dir_ = _TENANTS_DIR / tenant_id / "reasoning_index"
    if not dir_.exists():
        return 0
    n = 0
    for path in sorted(dir_.glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = _load_json(path)
        n += len(data.get("reasoning_records", []))
    return n


def _embed_many_parallel(
    texts: list[str],
    *,
    concurrency: int,
    dry_run: bool,
) -> list[list[float] | None]:
    """Titan Embed v2 를 ThreadPoolExecutor 로 병렬 호출.

    반환: texts 순서 유지된 vector list (실패 None).
    """
    if dry_run:
        return [[0.0] * 1024 for _ in texts]
    results: list[list[float] | None] = [None] * len(texts)
    if not texts:
        return results

    def _one(i: int) -> tuple[int, list[float] | None]:
        try:
            v = _embed_text(texts[i])
            return i, v
        except Exception as e:  # noqa: BLE001
            logger.warning("embed 실패 idx=%d: %s", i, e)
            return i, None

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_one, i) for i in range(len(texts))]
        for fut in as_completed(futures):
            i, v = fut.result()
            results[i] = v
    return results


def _index_golden(
    store: AossStore,
    tenant_id: str,
    dry_run: bool,
    *,
    concurrency: int = DEFAULT_EMBED_CONCURRENCY,
    batch_size: int = DEFAULT_BULK_BATCH,
) -> tuple[int, int]:
    dir_ = _TENANTS_DIR / tenant_id / "golden_set"
    if not dir_.exists():
        _emit_progress(tenant_id, "golden", total=0, current=0, status="empty")
        return 0, 0
    total = _count_golden_total(tenant_id)
    _emit_progress(tenant_id, "golden", total=total, current=0, status="start")

    # 1) 모든 example 을 메모리에 수집
    examples: list[dict] = []
    for path in sorted(dir_.glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = _load_json(path)
        if not data:
            continue
        item_no = data.get("item_number")
        for ex in data.get("examples", []):
            if not ex.get("example_id"):
                continue
            ex["_item_number"] = item_no
            examples.append(ex)

    if not examples:
        _emit_progress(tenant_id, "golden", total=0, current=0, status="empty")
        return 0, 0

    ok = fail = skip = 0
    # 2) concurrency 수만큼 묶어서 embed + bulk upsert 반복
    chunk = max(batch_size, concurrency)
    for start in range(0, len(examples), chunk):
        batch = examples[start:start + chunk]
        texts = [f"{ex.get('segment_text','')} | {ex.get('rationale','')}" for ex in batch]
        vecs = _embed_many_parallel(texts, concurrency=concurrency, dry_run=dry_run)
        docs: list[tuple[str, dict]] = []
        for ex, vec in zip(batch, vecs):
            if vec is None:
                fail += 1
                continue
            rater = ex.get("rater_meta") or {}
            # 내용 해시 — example_id + segment + rationale + score 조합. 내용 바뀌면 새 doc.
            chash = _content_hash(
                ex.get("example_id"),
                ex.get("segment_text", ""),
                ex.get("rationale", ""),
                ex.get("score"),
                ex.get("score_bucket"),
            )
            doc = {
                "embedding": vec,
                "tenant_id": tenant_id,
                "item_number": ex.get("_item_number"),
                "example_id": ex.get("example_id"),
                "content_hash": chash,
                "score": ex.get("score"),
                "score_bucket": ex.get("score_bucket"),
                "intent": ex.get("intent", "*"),
                "segment_text": ex.get("segment_text", ""),
                "rationale": ex.get("rationale", ""),
                "rationale_tags": ex.get("rationale_tags", []),
                "rater_type": rater.get("rater_type"),
                "rater_source": rater.get("source"),
            }
            # external_id = tenant:example_id:hash — 내용 변경 시 자동으로 새 external_id
            docs.append((f"{tenant_id}:{ex.get('example_id')}:{chash}", doc))

        if docs and not dry_run:
            try:
                n, sk = store.bulk_upsert_with_stats(
                    docs, skip_existing=True, tenant_id=tenant_id
                )
                ok += n
                skip += sk
                # 진짜 실패 = 시도 - success - skipped
                fail += len(docs) - n - sk
            except Exception as e:  # noqa: BLE001
                logger.warning("golden bulk_upsert 실패: %s", e)
                fail += len(docs)
        elif docs and dry_run:
            ok += len(docs)

        _emit_progress(
            tenant_id, "golden",
            total=total, current=ok, skip=skip, fail=fail, status="indexing",
        )

    _emit_progress(
        tenant_id, "golden",
        total=total, current=ok, skip=skip, fail=fail, status="done",
    )
    return ok, fail


def _enumerate_bk_manuals(tenant_id: str) -> list[tuple[str, str, Path]]:
    """tenants/{tenant}/ 아래 모든 business_knowledge/manual.md 를 (channel, department, path) 형태로 enumerate.

    스캔 대상 경로 패턴:
      - tenants/{tid}/business_knowledge/manual.md           → channel="default", department="default" (메인)
      - tenants/{tid}/{channel}/business_knowledge/manual.md → department="default"
      - tenants/{tid}/{channel}/{dept}/business_knowledge/manual.md → 부서별 (정상 케이스)
    """
    out: list[tuple[str, str, Path]] = []
    tenant_dir = _TENANTS_DIR / tenant_id
    if not tenant_dir.exists():
        return out
    for path in tenant_dir.rglob("business_knowledge/manual.md"):
        try:
            rel = path.relative_to(tenant_dir).parts  # (... 'business_knowledge', 'manual.md')
        except ValueError:
            continue
        # 'business_knowledge' 위치 = depth 결정
        if len(rel) == 2:
            channel, department = "default", "default"
        elif len(rel) == 3:
            channel, department = rel[0], "default"
        elif len(rel) == 4:
            channel, department = rel[0], rel[1]
        else:
            continue
        out.append((channel, department, path))
    return out


def _index_business_knowledge(
    store: AossStore,
    tenant_id: str,
    dry_run: bool,
    *,
    concurrency: int = DEFAULT_EMBED_CONCURRENCY,
    batch_size: int = DEFAULT_BULK_BATCH,
) -> tuple[int, int]:
    """tenants/{tid}/**/business_knowledge/manual.md → 모든 부서 manual 색인 (병렬).

    각 chunk doc 에 (tenant_id, channel, department) 박아 retrieve_knowledge 측 필터와 정합.
    """
    ok = fail = 0
    manuals = _enumerate_bk_manuals(tenant_id)
    if not manuals:
        _emit_progress(tenant_id, "knowledge", total=0, current=0, status="empty")
        return 0, 0

    # 부서별로 chunk 수집
    all_chunks: list[tuple[str, str, Any]] = []  # (channel, department, chunk)
    for channel, department, path in manuals:
        try:
            engine = BusinessKnowledgeRAG(
                tenant_id=tenant_id, channel=channel, department=department
            )
            # manual_path override 가 없으므로 _load_chunks 가 resolve_tenant_subdir 결과를 사용.
            # 여기서는 직접 path 를 지정해 강제 로드.
            engine._manual_path = str(path)
            engine._chunks = None  # 캐시 초기화
            chunks = engine._load_chunks()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "tenant=%s ch=%s dept=%s manual 로드 실패: %s — skip",
                tenant_id, channel, department, e,
            )
            continue
        for c in chunks:
            all_chunks.append((channel, department, c))
        logger.info(
            "tenant=%s ch=%s dept=%s manual=%s → %d chunk",
            tenant_id, channel, department, path.name, len(chunks),
        )

    if not all_chunks:
        _emit_progress(tenant_id, "knowledge", total=0, current=0, status="empty")
        return 0, 0

    total = len(all_chunks)
    _emit_progress(tenant_id, "knowledge", total=total, current=0, status="start")

    skip = 0
    chunk = max(batch_size, concurrency)
    for start in range(0, len(all_chunks), chunk):
        batch = all_chunks[start:start + chunk]
        texts = [f"{c.text} | {' '.join(c.tags or [])}" for _, _, c in batch]
        vecs = _embed_many_parallel(texts, concurrency=concurrency, dry_run=dry_run)
        docs: list[tuple[str, dict]] = []
        for (channel, department, c), vec in zip(batch, vecs):
            if vec is None:
                fail += 1
                continue
            title = (c.text.splitlines()[0] if c.text else "").strip()
            chash = _content_hash(
                c.chunk_id, c.text, c.tags or [], c.source_ref or "",
                channel, department,
            )
            doc = {
                "embedding": vec,
                "tenant_id": tenant_id,
                "channel": channel,
                "department": department,
                "chunk_id": c.chunk_id,
                "content_hash": chash,
                "title": title,
                "text": c.text,
                "intents": c.intents or [],
                "tags": c.tags or [],
                "source_ref": c.source_ref or "",
            }
            docs.append(
                (f"{tenant_id}:{channel}:{department}:{c.chunk_id}:{chash}", doc)
            )

        if docs and not dry_run:
            try:
                n, sk = store.bulk_upsert_with_stats(
                    docs, skip_existing=True, tenant_id=tenant_id
                )
                ok += n
                skip += sk
                fail += len(docs) - n - sk
            except Exception as e:  # noqa: BLE001
                logger.warning("BK bulk_upsert 실패: %s", e)
                fail += len(docs)
        elif docs and dry_run:
            ok += len(docs)

        _emit_progress(
            tenant_id, "knowledge",
            total=total, current=ok, skip=skip, fail=fail, status="indexing",
        )

    _emit_progress(
        tenant_id, "knowledge",
        total=total, current=ok, skip=skip, fail=fail, status="done",
    )
    return ok, fail


def _index_reasoning(
    store: AossStore,
    tenant_id: str,
    dry_run: bool,
    *,
    concurrency: int = DEFAULT_EMBED_CONCURRENCY,
    batch_size: int = DEFAULT_BULK_BATCH,
) -> tuple[int, int]:
    dir_ = _TENANTS_DIR / tenant_id / "reasoning_index"
    if not dir_.exists():
        _emit_progress(tenant_id, "reasoning", total=0, current=0, status="empty")
        return 0, 0
    total = _count_reasoning_total(tenant_id)
    _emit_progress(tenant_id, "reasoning", total=total, current=0, status="start")

    records: list[dict] = []
    for path in sorted(dir_.glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = _load_json(path)
        if not data:
            continue
        item_no = data.get("item_number")
        for rec in data.get("reasoning_records", []):
            if not rec.get("record_id"):
                continue
            rec["_item_number"] = item_no
            records.append(rec)

    if not records:
        _emit_progress(tenant_id, "reasoning", total=0, current=0, status="empty")
        return 0, 0

    ok = fail = skip = 0
    chunk = max(batch_size, concurrency)
    for start in range(0, len(records), chunk):
        batch = records[start:start + chunk]
        texts = [f"{rec.get('rationale','')} | {rec.get('quote_example','')}" for rec in batch]
        vecs = _embed_many_parallel(texts, concurrency=concurrency, dry_run=dry_run)
        docs: list[tuple[str, dict]] = []
        for rec, vec in zip(batch, vecs):
            if vec is None:
                fail += 1
                continue
            chash = _content_hash(
                rec.get("record_id"),
                rec.get("rationale", ""),
                rec.get("quote_example", ""),
                rec.get("score"),
            )
            doc = {
                "embedding": vec,
                "tenant_id": tenant_id,
                "item_number": rec.get("_item_number"),
                "record_id": rec.get("record_id"),
                "content_hash": chash,
                "score": rec.get("score"),
                "rationale": rec.get("rationale", ""),
                "quote_example": rec.get("quote_example", ""),
                "evaluator_id": rec.get("evaluator_id"),
                "rationale_tags": rec.get("tags", []),
            }
            docs.append((f"{tenant_id}:{rec.get('record_id')}:{chash}", doc))

        if docs and not dry_run:
            try:
                n, sk = store.bulk_upsert_with_stats(
                    docs, skip_existing=True, tenant_id=tenant_id
                )
                ok += n
                skip += sk
                fail += len(docs) - n - sk
            except Exception as e:  # noqa: BLE001
                logger.warning("reasoning bulk_upsert 실패: %s", e)
                fail += len(docs)
        elif docs and dry_run:
            ok += len(docs)

        _emit_progress(
            tenant_id, "reasoning",
            total=total, current=ok, skip=skip, fail=fail, status="indexing",
        )

    _emit_progress(
        tenant_id, "reasoning",
        total=total, current=ok, skip=skip, fail=fail, status="done",
    )
    return ok, fail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", help="특정 tenant(=site_id) 만 (기본: 모든 tenants/)")
    # 3단계 멀티테넌트 (2026-04-24). 현재는 옵션만 받아 로깅/메타 필드로 사용하고,
    # 색인 시 site_id/channel/department 를 doc 필드로 저장해 AOSS 필터와 정합.
    ap.add_argument("--site", help="site_id (=--tenant 과 동일 의미, 신규 이름)")
    ap.add_argument("--channel", help="inbound / outbound (3단계 중분류)")
    ap.add_argument("--department", help="부서 코드 (3단계 소분류, 자유 문자열)")
    ap.add_argument("--recreate", action="store_true", help="인덱스 삭제 후 재생성 (전체 tenant 영향)")
    ap.add_argument("--clean-tenant", action="store_true",
                    help="해당 tenant 의 기존 docs 만 _delete_by_query 로 정리 후 재색인 (다른 tenant 보존)")
    ap.add_argument("--dry-run", action="store_true", help="임베딩만 확인, 업로드 안함")
    ap.add_argument(
        "--concurrency", type=int, default=DEFAULT_EMBED_CONCURRENCY,
        help=f"Titan embed 동시 호출 수 (기본 {DEFAULT_EMBED_CONCURRENCY})",
    )
    ap.add_argument(
        "--batch-size", type=int, default=DEFAULT_BULK_BATCH,
        help=f"AOSS _bulk 배치당 문서 수 (기본 {DEFAULT_BULK_BATCH})",
    )
    args = ap.parse_args()

    if get_backend() != "titan":
        logger.error("QA_RAG_EMBEDDING=titan 이 아님 — bootstrap 은 Titan 필요. env 설정 후 재실행.")
        return 2

    # 3단계 멀티테넌트 — --site 우선, 없으면 레거시 --tenant, 그래도 없으면 전체.
    site_id = args.site or args.tenant
    channel = args.channel
    department = args.department
    tenants = [site_id] if site_id else _list_tenants()
    if not tenants:
        logger.error("tenant 디렉토리 없음: %s", _TENANTS_DIR)
        return 1
    logger.info(
        "대상 tenant: %s · channel=%s · department=%s",
        tenants, channel or "(전체)", department or "(전체)",
    )

    golden = AossStore(GOLDEN_INDEX)
    reasoning = AossStore(REASONING_INDEX)
    knowledge = AossStore(KNOWLEDGE_INDEX)

    if args.recreate and not args.dry_run:
        for st in (golden, reasoning, knowledge):
            try:
                st.delete_index()
            except Exception as e:  # noqa: BLE001
                logger.info("삭제 스킵 %s: %s", st.index_name, e)

    if not args.dry_run:
        golden.ensure_index()
        reasoning.ensure_index()
        knowledge.ensure_index()

    total_g_ok = total_g_fail = total_r_ok = total_r_fail = total_b_ok = total_b_fail = 0
    for t in tenants:
        # --clean-tenant : 해당 tenant 의 기존 docs 만 정리 + eventual consistency 대기
        if args.clean_tenant and not args.dry_run:
            for st in (golden, reasoning, knowledge):
                try:
                    n = st.delete_by_tenant(t)
                    logger.info("[clean-tenant] %s :: %s deleted=%d", t, st.index_name, n)
                    # AOSS Serverless 는 delete_by_query 가 eventual consistency — count 0 될 때까지 폴링
                    if n > 0 and not st.wait_until_tenant_empty(t, timeout_sec=60.0):
                        logger.warning("[clean-tenant] %s :: %s count 0 대기 timeout — 중복 생길 수 있음",
                                       t, st.index_name)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[clean-tenant] %s :: %s 삭제 실패 (AOSS Serverless 미지원 가능) — 중복 누적 위험: %s",
                                   t, st.index_name, str(e)[:200])
        g_ok, g_fail = _index_golden(
            golden, t, args.dry_run,
            concurrency=args.concurrency, batch_size=args.batch_size,
        )
        r_ok, r_fail = _index_reasoning(
            reasoning, t, args.dry_run,
            concurrency=args.concurrency, batch_size=args.batch_size,
        )
        b_ok, b_fail = _index_business_knowledge(
            knowledge, t, args.dry_run,
            concurrency=args.concurrency, batch_size=args.batch_size,
        )
        logger.info("tenant=%s golden ok=%d fail=%d | reasoning ok=%d fail=%d | BK ok=%d fail=%d",
                    t, g_ok, g_fail, r_ok, r_fail, b_ok, b_fail)
        total_g_ok += g_ok; total_g_fail += g_fail
        total_r_ok += r_ok; total_r_fail += r_fail
        total_b_ok += b_ok; total_b_fail += b_fail

    logger.info("=" * 60)
    logger.info("TOTAL golden    ok=%d fail=%d", total_g_ok, total_g_fail)
    logger.info("TOTAL reasoning ok=%d fail=%d", total_r_ok, total_r_fail)
    logger.info("TOTAL BK        ok=%d fail=%d", total_b_ok, total_b_fail)
    logger.info("endpoint=%s", golden.endpoint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
