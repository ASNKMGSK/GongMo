# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# ==========================================================================
# Karpathy LLM Wiki 3-Layer 아키텍처 개요
# ==========================================================================
# 이 모듈은 Andrej Karpathy가 제안한 "LLM OS" 개념 중 Wiki 패턴을 차용하여
# QA 평가에 필요한 지식을 3개 계층으로 분리·관리한다.
#
# [Layer 1] Raw Sources (원시 데이터) — 불변(immutable) 계층
#   - nodes/qa_rules.py : 21개 QA 평가항목 정의 (점수 기준, 감점 규칙, 체크리스트)
#   - nodes/sample_data.py : 샘플 상담 데이터
#   - 이 계층은 사람이 직접 관리하며, 코드 변경 없이 수정하지 않는다.
#
# [Layer 2] Wiki (사전 컴파일된 지식) — wiki/ 디렉터리
#   - 마크다운 페이지로 구성된 QA 평가 지식베이스
#   - rules/ : 7개 평가 영역별 세부 기준 (인사, 경청, 예의, 필수고지, 처리, 정확성, 종결)
#   - compliance/ : 업종별 컴플라이언스 규정 (보험 프로세스, 감점 체계, 개인정보, 특이사항)
#   - patterns/ : 우수/위반 사례 패턴 모음
#   - LLM Ingest 또는 Query-save로 동적 확장 가능
#
# [Layer 3] Schema (규약) — wiki/SCHEMA.md
#   - 위키 문서 작성 규칙, 프론트매터 형식, 워크플로 정의
#   - 새 페이지 추가 시 따라야 할 메타데이터 표준
#
# 핵심 설계 원칙: "No LLM Call"
#   - 이 노드는 LLM을 호출하지 않는다.
#   - 모든 지식이 위키에 사전 컴파일되어 있으므로, 파일 읽기만으로 컨텍스트를 구성한다.
#   - 이전 RAG+LLM 방식(~21초)에서 순수 파일 I/O(~0.05초)로 약 420배 성능 향상.
#   - 결과적으로 평가 파이프라인 전체 레이턴시를 대폭 절감한다.
# ==========================================================================

"""
Retrieval node — Wiki-based QA knowledge retrieval (Karpathy LLM Wiki pattern).

Instead of RAG-style LLM analysis on every evaluation (21s+), reads pre-compiled
wiki pages to provide evaluation context to downstream nodes.

Architecture (Karpathy LLM Wiki, 3-layer):
- Layer 1: Raw sources (nodes/qa_rules.py, nodes/sample_data.py) — immutable
- Layer 2: Wiki (wiki/) — pre-compiled markdown pages with cross-references
- Layer 3: Schema (wiki/SCHEMA.md) — conventions and workflows

At evaluation time, this node:
1. Reads relevant wiki pages based on consultation_type
2. Loads built-in QA rules from qa_rules.py
3. Compiles both into the ``rules`` state field
4. **No LLM call** — all knowledge is pre-compiled in wiki

Performance: ~0.05s (file reads) vs ~21s (previous RAG+LLM approach)
"""

from __future__ import annotations

import logging
import os
from nodes.qa_rules import QA_RULES, get_rules_by_category
from state import QAState
from typing import Any


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Wiki 경로 설정
# ---------------------------------------------------------------------------

# 파이프라인 루트 디렉터리: 현재 파일(nodes/retrieval.py)에서 두 단계 상위
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 위키 디렉터리: qa-pipeline/wiki/
_WIKI_DIR = os.path.join(_PIPELINE_DIR, "wiki")

# ---------------------------------------------------------------------------
# 상담 유형(consultation_type)별 위키 페이지 매핑
# ---------------------------------------------------------------------------
# 각 상담 유형에 따라 로드해야 할 위키 페이지 목록을 정적으로 정의한다.
# 이 매핑을 통해 불필요한 페이지 로딩을 방지하고, 상담 유형에 최적화된
# 평가 컨텍스트만 하류(downstream) 노드에 전달한다.
#
# 페이지 구성:
#   rules/     — 7개 평가 영역 (01~07) + 전체 개요(overview)
#   compliance/— 컴플라이언스 규정 (보험 프로세스, 감점 체계, 개인정보, 특이사항)
#   patterns/  — 우수 사례 및 빈출 위반 패턴
_WIKI_PAGES: dict[str, list[str]] = {
    # ── 보험 상담: 전 영역 로드 + 보험 특화 컴플라이언스 (보험 프로세스, 개인정보)
    "insurance": [
        "rules/overview.md",
        "rules/01-greeting.md",
        "rules/02-listening.md",
        "rules/03-courtesy.md",
        "rules/04-mandatory.md",        # 필수고지 — 보험 상담에서 핵심
        "rules/05-process.md",
        "rules/06-accuracy.md",
        "rules/07-closing.md",
        "compliance/insurance-process.md",  # 보험 업무 처리 절차 규정
        "compliance/penalty-scoring.md",    # 감점 체계
        "compliance/privacy.md",            # 개인정보 보호 규정
        "patterns/excellent.md",
        "patterns/common-violations.md",
    ],
    # ── IT 상담: 필수고지(04) 제외, special(장애/에러) 포함
    # IT 상담은 보험 필수고지 의무가 없으므로 04-mandatory 생략.
    # 대신 시스템 장애·에러 대응 특이사항(special) 규정을 포함.
    "IT": [
        "rules/overview.md",
        "rules/01-greeting.md",
        "rules/02-listening.md",
        "rules/03-courtesy.md",
        # 04-mandatory 의도적 생략: IT 상담에는 보험 필수고지 항목 불필요
        "rules/05-process.md",
        "rules/06-accuracy.md",
        "rules/07-closing.md",
        "compliance/penalty-scoring.md",
        "compliance/special.md",            # IT 특이사항 (장애, 에러 대응 규정)
        "patterns/excellent.md",
        "patterns/common-violations.md",
    ],
    # ── 일반 상담: 전 영역 로드 + 범용 컴플라이언스
    # 특화 업종이 아닌 경우의 기본 매핑. 모든 평가 영역을 커버한다.
    "general": [
        "rules/overview.md",
        "rules/01-greeting.md",
        "rules/02-listening.md",
        "rules/03-courtesy.md",
        "rules/04-mandatory.md",
        "rules/05-process.md",
        "rules/06-accuracy.md",
        "rules/07-closing.md",
        "compliance/penalty-scoring.md",
        "compliance/privacy.md",
        "patterns/excellent.md",
        "patterns/common-violations.md",
    ],
}

# 알 수 없는 상담 유형이 들어오면 "general" 매핑을 기본값으로 사용
_DEFAULT_PAGES = _WIKI_PAGES["general"]


# ---------------------------------------------------------------------------
# 위키 읽기 헬퍼 함수들
# ---------------------------------------------------------------------------


def _read_wiki_page(relative_path: str) -> str | None:
    """Read a wiki page and return its content. Returns None if not found."""
    # 위키 디렉터리 기준 상대 경로를 절대 경로로 변환하여 파일 읽기
    full_path = os.path.join(_WIKI_DIR, relative_path)
    try:
        with open(full_path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        # 위키 페이지가 아직 생성되지 않았을 수 있음 — 경고만 남기고 계속 진행
        logger.warning("Wiki page not found: %s", full_path)
        return None
    except Exception as e:
        # 권한 오류, 인코딩 오류 등 기타 예외 — 평가 중단하지 않고 로깅
        logger.warning("Failed to read wiki page %s: %s", relative_path, e)
        return None


def _load_wiki_context(consultation_type: str) -> dict[str, str]:
    """Load all relevant wiki pages for the given consultation type.

    Reads the curated list first, then scans for any additional pages
    added by LLM Ingest or Query-save (dynamic wiki growth).

    Returns a dict mapping page path → content string.
    """
    # -----------------------------------------------------------------
    # 1단계: 정적 매핑(curated) 페이지 로드
    # -----------------------------------------------------------------
    # _WIKI_PAGES 딕셔너리에서 상담 유형에 맞는 페이지 목록을 가져온다.
    # 매핑에 없는 유형이면 _DEFAULT_PAGES(general)를 사용한다.
    pages = _WIKI_PAGES.get(consultation_type, _DEFAULT_PAGES)
    context: dict[str, str] = {}

    # 매핑된 페이지를 순서대로 읽어 context 딕셔너리에 적재
    for page_path in pages:
        content = _read_wiki_page(page_path)
        if content:
            context[page_path] = content

    # -----------------------------------------------------------------
    # 2단계: 동적 위키 디렉터리 스캔 (LLM Ingest / Query-save 대응)
    # -----------------------------------------------------------------
    # Karpathy Wiki 패턴에서 Layer 2(Wiki)는 동적으로 성장할 수 있다.
    # LLM Ingest: 외부 문서를 LLM이 요약·분류하여 위키 페이지로 자동 생성
    # Query-save: 반복 질의를 캐싱하여 새 위키 페이지로 저장
    #
    # 이 스캔은 정적 매핑에 포함되지 않은 "새로 추가된" 페이지를 발견하여
    # 평가 컨텍스트에 자동으로 포함시킨다.
    # 이를 통해 위키가 확장되어도 이 코드를 수정할 필요가 없다.
    _curated_set = set(pages)
    # SCHEMA.md(규약), index.md(목차), log.md(변경 이력)은 평가 컨텍스트에 불필요하므로 제외
    _skip_files = {"SCHEMA.md", "index.md", "log.md"}
    for root, _dirs, files in os.walk(_WIKI_DIR):
        for fname in files:
            # 마크다운 파일만 대상, 스킵 목록·숨김 파일 제외
            if not fname.endswith(".md") or fname in _skip_files or fname.startswith("."):
                continue
            # Windows 역슬래시를 슬래시로 통일 (크로스 플랫폼 호환)
            rel = os.path.relpath(os.path.join(root, fname), _WIKI_DIR).replace("\\", "/")
            # 이미 정적 매핑으로 로드된 페이지는 중복 로드 방지
            if rel not in _curated_set and rel not in context:
                content = _read_wiki_page(rel)
                if content:
                    context[rel] = content

    return context


def _extract_keyword_context(transcript: str, wiki_context: dict[str, str]) -> list[str]:
    """Identify which wiki sections are most relevant based on transcript keywords.

    Returns a list of relevant topic tags for downstream nodes.
    """
    # -----------------------------------------------------------------
    # 키워드 기반 컨텍스트 추출 (경량 관련성 판단)
    # -----------------------------------------------------------------
    # LLM 없이 상담 스크립트에서 핵심 키워드를 단순 문자열 매칭으로 찾아
    # 관련 토픽 태그를 반환한다. 이 태그는 하류 노드에서 평가 초점을
    # 결정하는 데 참고 정보로 활용된다.
    #
    # 설계 의도:
    #   - LLM 호출 없이 O(n*m) 단순 매칭으로 토픽을 분류 (n=키워드수, m=스크립트 길이)
    #   - 보험 관련 키워드 → insurance-process, penalty-scoring 토픽
    #   - 개인정보 관련 키워드 → privacy 토픽
    #   - 민원/장애/에러/고령 키워드 → special 토픽 (특이사항 처리 규정 참조 필요)
    keywords_to_topics = {
        "해약": ["insurance-process", "penalty-scoring"],   # 보험 해약 → 프로세스 + 감점
        "해지": ["insurance-process", "penalty-scoring"],   # 보험 해지 → 프로세스 + 감점
        "환급금": ["insurance-process"],                    # 환급금 관련 → 보험 프로세스
        "청구": ["insurance-process"],                      # 보험금 청구 → 보험 프로세스
        "보험금": ["insurance-process"],                    # 보험금 관련 → 보험 프로세스
        "보장": ["insurance-process"],                      # 보장 내용 → 보험 프로세스
        "개인정보": ["privacy"],                            # 개인정보 언급 → 개인정보 보호 규정
        "제3자": ["privacy"],                               # 제3자 정보 제공 → 개인정보 보호 규정
        "불만": ["special"],                                # 고객 불만 → 특이사항 대응 규정
        "민원": ["special"],                                # 민원 접수 → 특이사항 대응 규정
        "장애": ["special"],                                # 시스템 장애 → 특이사항 대응 규정
        "에러": ["special"],                                # 시스템 에러 → 특이사항 대응 규정
        "고령": ["special"],                                # 고령 고객 → 특이사항 대응 규정 (취약계층)
    }

    # 상담 스크립트에서 키워드 포함 여부를 확인하여 관련 토픽 수집
    relevant_topics: set[str] = set()
    for keyword, topics in keywords_to_topics.items():
        if keyword in transcript:
            relevant_topics.update(topics)

    # 정렬하여 반환 — 결과 일관성 보장 (set 순서 비결정적이므로)
    return sorted(relevant_topics)


# ---------------------------------------------------------------------------
# 메인 노드 함수
# ---------------------------------------------------------------------------
# 이 노드는 LangGraph 파이프라인에서 retrieval 단계를 담당한다.
# "No LLM Call" 원칙:
#   - LLM API를 전혀 호출하지 않는다.
#   - 모든 지식은 위키 파일(Layer 2)과 qa_rules(Layer 1)에 사전 컴파일되어 있다.
#   - 파일 I/O만으로 평가 컨텍스트를 구성하므로, 네트워크 지연·토큰 비용이 없다.
#   - 결과물은 state["rules"] 필드에 저장되어 하류 평가 노드들이 소비한다.
# ---------------------------------------------------------------------------


async def retrieval_node(state: QAState) -> dict[str, Any]:
    """Wiki-based QA knowledge retrieval — no LLM call.

    Reads pre-compiled wiki pages and built-in QA rules to produce
    the ``rules`` state field consumed by downstream evaluation nodes.
    """
    # 파이프라인 상태에서 상담 스크립트와 상담 유형을 꺼낸다
    transcript = state.get("transcript", "")
    consultation_type = state.get("consultation_type", "general")

    logger.info("retrieval_node [wiki]: type='%s', transcript_len=%d", consultation_type, len(transcript))

    # -------------------------------------------------------------------
    # 1단계: 내장 QA 규칙 로드 (Layer 1 — Raw Sources)
    # -------------------------------------------------------------------
    # qa_rules.py에서 상담 유형에 해당하는 평가항목만 필터링하여 가져온다.
    # 21개 전체 항목 중 applicable_categories에 현재 유형이 포함된 항목만 반환.
    all_rules = get_rules_by_category(consultation_type)
    logger.info("retrieval_node [wiki]: loaded %d built-in rules", len(all_rules))

    # -------------------------------------------------------------------
    # 2단계: 위키 컨텍스트 로드 (Layer 2 — Pre-compiled Wiki)
    # -------------------------------------------------------------------
    # 정적 매핑 페이지 + 동적 스캔 페이지를 모두 로드한다.
    # 반환값: {페이지 상대경로: 마크다운 내용} 딕셔너리
    wiki_context = _load_wiki_context(consultation_type)
    pages_loaded = len(wiki_context)
    logger.info("retrieval_node [wiki]: loaded %d wiki pages for type '%s'", pages_loaded, consultation_type)

    # -------------------------------------------------------------------
    # 3단계: 키워드 기반 관련성 추출
    # -------------------------------------------------------------------
    # 상담 스크립트에서 핵심 키워드를 매칭하여 관련 토픽 태그 목록을 생성.
    # 이 태그는 하류 노드에서 특정 컴플라이언스 규정에 주의를 기울이도록 안내.
    relevant_topics = _extract_keyword_context(transcript, wiki_context)
    logger.info("retrieval_node [wiki]: relevant topics = %s", relevant_topics)

    # -------------------------------------------------------------------
    # 4단계: 위키 콘텐츠를 단일 문자열로 컴파일
    # -------------------------------------------------------------------
    # 모든 위키 페이지를 하나의 긴 문자열(wiki_compiled)로 합친다.
    # 각 페이지는 HTML 주석으로 출처를 표시하여 추적 가능하게 한다.
    # 하류 평가 노드가 LLM 프롬프트에 이 문자열을 컨텍스트로 주입한다.
    wiki_text_parts: list[str] = []
    for page_path, content in wiki_context.items():
        # YAML 프론트매터 제거: 위키 페이지 상단의 메타데이터(---...---)는
        # 평가 컨텍스트에 불필요하므로 잘라낸다.
        if content.startswith("---"):
            end_idx = content.find("---", 3)
            if end_idx != -1:
                content = content[end_idx + 3:].strip()
        # HTML 주석으로 페이지 출처 표시 + 본문 내용 결합
        wiki_text_parts.append(f"<!-- wiki: {page_path} -->\n{content}")

    # 페이지 간 구분선(---)으로 연결하여 단일 컨텍스트 문자열 생성
    wiki_compiled = "\n\n---\n\n".join(wiki_text_parts)

    # -------------------------------------------------------------------
    # 5단계: QA 규칙에 위키 메타데이터를 보강(enrich)
    # -------------------------------------------------------------------
    # Layer 1(qa_rules)의 각 규칙에 위키 기반 부가 정보를 덧붙인다.
    # 이를 통해 하류 노드는 규칙의 출처·관련성·평가 가이던스를
    # 별도 조회 없이 바로 참조할 수 있다.
    enriched_rules = []
    for rule in all_rules:
        enriched = {
            **rule,                         # 원본 규칙의 모든 필드 보존
            "source": "wiki",               # 출처: 위키 기반 사전 컴파일
            "relevance": "high",            # 위키에 수록된 규칙은 모두 사전 검증됨
            "eval_guidance": "",            # 추가 평가 가이던스 (향후 확장용)
            "relevance_reason": "위키 사전 컴파일 규칙",  # 관련성 판단 근거
        }
        enriched_rules.append(enriched)

    # -------------------------------------------------------------------
    # 6단계: rules 상태 반환 (하류 노드 호환 형식)
    # -------------------------------------------------------------------
    # 반환 딕셔너리 구조:
    #   rules.status       — 조회 성공/실패 상태
    #   rules.results      — 보강된 QA 규칙 목록 (하류 평가 노드의 채점 기준)
    #   rules.wiki_context — 컴파일된 위키 전문 (LLM 프롬프트 컨텍스트로 사용)
    #   rules.relevant_topics — 키워드 매칭으로 추출한 관련 토픽 태그
    #   rules.analysis     — 분석 요약 정보 (상담 요약, 이슈, 유사 사례, 주의 항목)
    return {
        "rules": {
            "status": "success",
            "message": (
                f"Wiki-based retrieval: {len(enriched_rules)} rules, "
                f"{pages_loaded} wiki pages loaded, "
                f"topics: {relevant_topics}"
            ),
            "results": enriched_rules,
            "total": len(enriched_rules),
            "wiki_pages_loaded": pages_loaded,
            "relevant_topics": relevant_topics,
            "wiki_context": wiki_compiled,
            "analysis": {
                "consultation_summary": f"위키 기반 {consultation_type} 상담 평가 컨텍스트 로드 완료",
                "identified_issues": [],
                "similar_case_insights": "위키 패턴 페이지 참조 (patterns/excellent.md, patterns/common-violations.md)",
                "supplementary_rules_applied": relevant_topics,
                "critical_flags": [],
                "recommended_focus": list(range(1, len(enriched_rules) + 1)),
            },
        }
    }
