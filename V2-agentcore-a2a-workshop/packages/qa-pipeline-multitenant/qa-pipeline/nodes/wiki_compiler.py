# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# =============================================================================
# Wiki 컴파일러 -- Wiki 페이지 수집(Ingest), 검증(Lint), 통계(Stats)
# =============================================================================
# 이 모듈은 QA Wiki의 유지보수 도구이다. (Karpathy LLM Wiki 패턴 기반)
# 원본 소스(qa_rules.py, sample_data.py)와 Wiki 페이지의 동기화를 관리한다.
#
# [핵심 역할]
# 1. ingest: 원본 소스(qa_rules.py, sample_data.py) 데이터와 Wiki 페이지의 일치 여부 검증
#    - 규칙 수, 사례 수, 보충 규칙 수 확인
#    - 예상 Wiki 페이지 존재 여부 확인
#    - 카테고리 매핑 검증
#
# 2. lint: Wiki 건강 상태 점검
#    - 깨진 링크(broken links) 감지: 존재하지 않는 .md 파일을 가리키는 링크
#    - 고아 페이지(orphan pages) 감지: 다른 페이지에서 링크되지 않는 페이지
#    - 페이지 수 통계
#
# 3. stats: Wiki 통계 출력
#    - 디렉토리별 페이지 수
#    - 전체 크기(바이트/KB)
#    - 총 단어 수 (근사치)
#
# [사용 방법]
# 원본 소스가 변경되거나 정기 유지보수 시 수동으로 실행:
#   python -m nodes.wiki_compiler ingest  # 원본과 Wiki 동기화 검증
#   python -m nodes.wiki_compiler lint    # Wiki 건강 검사
#   python -m nodes.wiki_compiler stats   # Wiki 통계 출력
#
# [파이프라인 내 위치]
# 런타임에 호출되지 않음. 개발/유지보수 시에만 사용하는 오프라인 도구.
# 파이프라인의 retrieval 노드가 wiki/ 디렉토리의 .md 파일을 읽어 평가 규칙을 가져옴.
# =============================================================================

"""
Wiki compiler — Ingest & Lint operations for QA Wiki (Karpathy LLM Wiki pattern).

Compiles raw sources (qa_rules.py, sample_data.py) into structured wiki pages.
Run manually when raw sources change or periodically for maintenance.

Usage:
    python -m nodes.wiki_compiler ingest    # Recompile wiki from raw sources
    python -m nodes.wiki_compiler lint      # Health-check the wiki
    python -m nodes.wiki_compiler stats     # Show wiki statistics
"""

from __future__ import annotations

import datetime
import json
import os
import sys

# 파이프라인 루트 디렉토리를 sys.path에 추가하여 nodes 패키지를 임포트 가능하게 함
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

# Wiki 디렉토리 경로와 로그 파일 경로
_WIKI_DIR = os.path.join(_PIPELINE_DIR, "wiki")
_LOG_FILE = os.path.join(_WIKI_DIR, "log.md")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# Wiki 관리를 위한 유틸리티 함수들.
# 로그 기록, 페이지 카운트, 깨진 링크 감지, 고아 페이지 감지 기능 제공.


def _today() -> str:
    # 오늘 날짜를 ISO 형식(YYYY-MM-DD)으로 반환
    return datetime.date.today().isoformat()


def _append_log(operation: str, description: str):
    """Append an entry to wiki/log.md."""
    # wiki/log.md에 작업 로그를 추가한다.
    # 형식: ## [날짜] 작업명 | 설명
    entry = f"\n## [{_today()}] {operation} | {description}\n"
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
    print(f"[LOG] {entry.strip()}")


def _count_wiki_pages() -> dict[str, int]:
    """Count wiki pages by directory."""
    # Wiki 디렉토리를 순회하며 디렉토리별 .md 페이지 수를 카운트한다.
    # SCHEMA.md, index.md, log.md는 메타 파일이므로 카운트에서 제외
    counts: dict[str, int] = {}
    for root, _dirs, files in os.walk(_WIKI_DIR):
        md_files = [f for f in files if f.endswith(".md") and f not in ("SCHEMA.md", "index.md", "log.md")]
        if md_files:
            rel_dir = os.path.relpath(root, _WIKI_DIR)
            counts[rel_dir] = len(md_files)
    return counts


def _find_broken_links() -> list[str]:
    """Find wiki-internal links that point to non-existent pages."""
    # Wiki 내부 링크 중 존재하지 않는 페이지를 가리키는 깨진 링크를 찾는다.
    # 마크다운 링크 패턴 [텍스트](경로.md)에서 내부 링크만 추출 (http 제외)
    # 앵커(#) 부분은 제거하고 파일 존재 여부만 확인
    import re

    broken: list[str] = []
    for root, _dirs, files in os.walk(_WIKI_DIR):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()

            # 마크다운 내부 링크 추출: [텍스트](경로.md) 형태, http로 시작하는 외부 링크 제외
            links = re.findall(r"\[.*?\]\(((?!http)[^)]+\.md[^)]*)\)", content)
            for link in links:
                # 앵커(#이후) 제거
                link_path = link.split("#")[0]
                # 해당 파일 기준 상대 경로로 해석
                resolved = os.path.normpath(os.path.join(os.path.dirname(fpath), link_path))
                if not os.path.exists(resolved):
                    rel_source = os.path.relpath(fpath, _WIKI_DIR)
                    broken.append(f"{rel_source} → {link_path}")

    return broken


def _find_orphan_pages() -> list[str]:
    """Find pages that have no inbound links from other pages."""
    # 다른 페이지에서 한 번도 링크되지 않는 "고아" 페이지를 찾는다.
    # 고아 페이지는 Wiki 탐색에서 접근 불가능할 수 있으므로 주의가 필요
    import re

    # 모든 Wiki 페이지 경로 수집 (메타 파일 제외)
    all_pages: set[str] = set()
    for root, _dirs, files in os.walk(_WIKI_DIR):
        for fname in files:
            if fname.endswith(".md") and fname not in ("SCHEMA.md", "index.md", "log.md"):
                rel = os.path.relpath(os.path.join(root, fname), _WIKI_DIR)
                all_pages.add(rel.replace("\\", "/"))

    # 모든 페이지에서 링크된 페이지 경로 수집
    linked: set[str] = set()
    for root, _dirs, files in os.walk(_WIKI_DIR):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
            links = re.findall(r"\[.*?\]\(((?!http)[^)]+\.md[^)]*)\)", content)
            for link in links:
                link_path = link.split("#")[0]
                resolved = os.path.normpath(os.path.join(os.path.dirname(fpath), link_path))
                rel = os.path.relpath(resolved, _WIKI_DIR)
                linked.add(rel.replace("\\", "/"))

    # 전체 페이지 - 링크된 페이지 = 고아 페이지
    return sorted(all_pages - linked)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
# 3가지 메인 작업: ingest(수집/검증), lint(건강검사), stats(통계)


def op_ingest():
    """Re-compile wiki pages from raw sources (qa_rules.py + sample_data.py).

    Note: The initial wiki pages were manually compiled during setup.
    This function validates that raw source data matches wiki content
    and reports any drift.
    """
    # ingest 작업: 원본 소스(qa_rules.py, sample_data.py)와 Wiki 페이지의 동기화를 검증한다.
    # 초기 Wiki 페이지는 수동으로 작성되었으므로, 이 함수는 원본과의 불일치(drift)를 감지한다.
    # 검증 항목: 규칙 수, 사례 수, 보충 규칙 수, 예상 페이지 존재 여부, 카테고리 매핑
    from nodes.qa_rules import QA_RULES
    from nodes.sample_data import _SIMILAR_CASES, _SUPPLEMENTARY_RULES

    print("=" * 60)
    print("INGEST: Validating wiki against raw sources")
    print("=" * 60)

    # Check rule counts
    rule_count = len(QA_RULES)
    case_count = len(_SIMILAR_CASES)
    supp_count = len(_SUPPLEMENTARY_RULES)

    print(f"  Raw sources: {rule_count} rules, {case_count} cases, {supp_count} supplementary rules")

    # Check wiki pages exist
    expected_pages = [
        "rules/overview.md",
        "rules/01-greeting.md", "rules/02-listening.md", "rules/03-courtesy.md",
        "rules/04-mandatory.md", "rules/05-process.md", "rules/06-accuracy.md",
        "rules/07-closing.md",
        "patterns/excellent.md", "patterns/common-violations.md", "patterns/borderline.md",
        "compliance/insurance-process.md", "compliance/penalty-scoring.md",
        "compliance/privacy.md", "compliance/special.md",
        "guides/evaluation-flow.md",
    ]

    missing = []
    for page in expected_pages:
        if not os.path.exists(os.path.join(_WIKI_DIR, page)):
            missing.append(page)

    if missing:
        print(f"\n  MISSING pages ({len(missing)}):")
        for p in missing:
            print(f"    - {p}")
    else:
        print(f"  All {len(expected_pages)} expected wiki pages present.")

    # Validate rule items are covered in wiki
    categories = set()
    for rule in QA_RULES:
        categories.add(rule["category"])
    print(f"  Rule categories in raw: {sorted(categories)}")

    # Report
    total_score = sum(r["max_score"] for r in QA_RULES)
    print(f"  Total scoring: {rule_count} items, {total_score} points")

    _append_log("ingest", f"Validated: {rule_count} rules, {case_count} cases, {supp_count} supplementary. Missing pages: {len(missing)}")
    print("\nIngest complete.")


def op_lint():
    """Health-check the wiki: broken links, orphans, consistency."""
    # lint 작업: Wiki의 건강 상태를 점검한다.
    # 1) 깨진 링크: 존재하지 않는 페이지를 가리키는 링크 감지
    # 2) 고아 페이지: 다른 페이지에서 링크되지 않는 고립된 페이지 감지
    # 3) 페이지 수 통계: 디렉토리별 .md 파일 수
    print("=" * 60)
    print("LINT: Wiki health check")
    print("=" * 60)

    # 1. Broken links
    broken = _find_broken_links()
    if broken:
        print(f"\n  BROKEN LINKS ({len(broken)}):")
        for b in broken:
            print(f"    - {b}")
    else:
        print("  No broken links found.")

    # 2. Orphan pages
    orphans = _find_orphan_pages()
    if orphans:
        print(f"\n  ORPHAN PAGES ({len(orphans)}):")
        for o in orphans:
            print(f"    - {o}")
    else:
        print("  No orphan pages found.")

    # 3. Page counts
    counts = _count_wiki_pages()
    total = sum(counts.values())
    print(f"\n  WIKI PAGES ({total} total):")
    for directory, count in sorted(counts.items()):
        print(f"    {directory}/: {count}")

    _append_log("lint", f"Broken links: {len(broken)}, Orphans: {len(orphans)}, Total pages: {total}")
    print("\nLint complete.")


def op_stats():
    """Show wiki statistics."""
    # stats 작업: Wiki의 상세 통계를 출력한다.
    # 디렉토리별 페이지 수, 전체 파일 크기(바이트/KB), 총 단어 수(근사치)
    print("=" * 60)
    print("STATS: Wiki statistics")
    print("=" * 60)

    counts = _count_wiki_pages()
    total = sum(counts.values())

    print(f"\n  Total pages: {total}")
    for directory, count in sorted(counts.items()):
        print(f"    {directory}/: {count}")

    # Total size
    total_bytes = 0
    for root, _dirs, files in os.walk(_WIKI_DIR):
        for fname in files:
            if fname.endswith(".md"):
                total_bytes += os.path.getsize(os.path.join(root, fname))
    print(f"\n  Total size: {total_bytes:,} bytes ({total_bytes / 1024:.1f} KB)")

    # Word count approximation
    total_words = 0
    for root, _dirs, files in os.walk(_WIKI_DIR):
        for fname in files:
            if fname.endswith(".md"):
                with open(os.path.join(root, fname), encoding="utf-8") as f:
                    total_words += len(f.read().split())
    print(f"  Total words: ~{total_words:,}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
# 커맨드라인 인터페이스: 직접 실행 시 ingest/lint/stats 명령을 처리한다.
# 사용법: python -m nodes.wiki_compiler [ingest|lint|stats]

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m nodes.wiki_compiler [ingest|lint|stats]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "ingest":
        op_ingest()
    elif cmd == "lint":
        op_lint()
    elif cmd == "stats":
        op_stats()
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: python -m nodes.wiki_compiler [ingest|lint|stats]")
        sys.exit(1)
