# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Korean NER for LinearRAG — Tri-Graph entity 추출 모듈.

LinearRAG 논문 §3.1 은 spaCy `en_core_web_trf` 를 사용한다. V3 는 한국어 KMS 환경이므로:

  Backend 우선순위 (자동 fallback):
  1. spaCy `ko_core_news_lg`  — peer-reviewed, 정확도 best (~89% F1)
  2. Kiwi (kiwipiepy)         — 빠른 형태소 분석기 + 명사 추출 (~85% recall)
  3. 정규식 기반 키워드 추출  — 최후 수단 (KMS 표 키워드 사전 활용 가능)

환경변수:
    QA_LINEAR_NER  : "spacy" | "kiwi" | "regex" (기본 자동 감지)

KMS 표 데이터의 특성상 "필수 키워드" 컬럼이 이미 entity 후보를 명시한다 →
  table-based corpus 의 경우 NER 외에 keyword vocabulary 를 추가 inject 하는
  hook (`additional_keywords`) 을 제공한다.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

_ENV_BACKEND = "QA_LINEAR_NER"

# spaCy 가 추출하는 entity label 중 KMS 도메인 관련 (PERSON/LOC/ORG/PRODUCT/EVENT/DATE/MONEY 등)
_SPACY_ALLOWED_LABELS = frozenset(
    [
        "PS",  # PERSON (한국어 spaCy 기준)
        "LC",  # LOCATION
        "OG",  # ORGANIZATION
        "AF",  # ARTIFACT (제품/물품)
        "DT",  # DATE
        "TI",  # TIME
        "CV",  # CIVILIZATION (제도, 절차)
        "AM",  # ANIMAL
        "PT",  # PLANT
        "QT",  # QUANTITY
        "FD",  # FIELD (학문, 분야)
        "TR",  # THEORY
        "EV",  # EVENT
        "MT",  # MATERIAL
        "TM",  # TERM (전문 용어)
        # 영어 라벨도 일부 호환 (klue/bert 같은 모델 결과 수용)
        "PERSON",
        "LOC",
        "ORG",
        "PRODUCT",
        "EVENT",
        "DATE",
        "MONEY",
        "WORK_OF_ART",
        "FAC",
    ]
)


@dataclass(frozen=True)
class ExtractedEntity:
    """NER 결과 단일 entity."""

    surface: str  # 원문 표면형
    canonical: str  # 정규화 (lowercase + strip + 동의어 매핑)
    label: str  # 라벨 (PS / LOC / ORG / ...)
    start: int  # 원문 char offset (선택적)
    end: int


class NERBackend(Protocol):
    """NER 백엔드 공통 인터페이스."""

    def extract(self, text: str) -> list[ExtractedEntity]: ...

    @property
    def name(self) -> str: ...


# ── Backend 1: spaCy ─────────────────────────────────────────────────


class SpacyKoreanNER:
    """spaCy `ko_core_news_lg` 기반 NER."""

    def __init__(self, model_name: str = "ko_core_news_lg"):
        try:
            import spacy  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "spacy 미설치 — `pip install spacy` + `python -m spacy download ko_core_news_lg`"
            ) from exc
        try:
            self._nlp = spacy.load(model_name, disable=["parser", "tagger", "lemmatizer"])
        except OSError as exc:
            raise RuntimeError(
                f"spaCy 모델 미설치: {model_name}. "
                f"실행: `python -m spacy download {model_name}`"
            ) from exc
        self._model_name = model_name

    @property
    def name(self) -> str:
        return f"spacy:{self._model_name}"

    def extract(self, text: str) -> list[ExtractedEntity]:
        if not text or not text.strip():
            return []
        doc = self._nlp(text)
        out: list[ExtractedEntity] = []
        for ent in doc.ents:
            label = ent.label_
            if label not in _SPACY_ALLOWED_LABELS:
                continue
            surface = ent.text.strip()
            if not surface or len(surface) < 2:
                continue
            out.append(
                ExtractedEntity(
                    surface=surface,
                    canonical=_canonicalize(surface),
                    label=label,
                    start=ent.start_char,
                    end=ent.end_char,
                )
            )
        return out


# ── Backend 2: Kiwi (kiwipiepy) ──────────────────────────────────────


class KiwiNER:
    """Kiwi 형태소 분석기 기반 명사 추출.

    엄밀한 NER 아니라 **명사구 추출** 으로 entity proxy. 한국어 KMS 의 경우 명사가
    대부분의 정보 단위라 충분히 작동. Kiwi 는 50MB 미만, fast.
    """

    # Kiwi POS 태그: NNP (고유명사), NNG (일반명사), SL (외국어), SH (한자)
    _NOUN_TAGS = ("NNP", "NNG", "SL", "SH")

    def __init__(self):
        try:
            from kiwipiepy import Kiwi  # type: ignore
        except ImportError as exc:
            raise RuntimeError("kiwipiepy 미설치 — `pip install kiwipiepy`") from exc
        self._kiwi = Kiwi()

    @property
    def name(self) -> str:
        return "kiwi"

    def extract(self, text: str) -> list[ExtractedEntity]:
        if not text or not text.strip():
            return []
        out: list[ExtractedEntity] = []
        # Kiwi 의 tokenize 결과는 Token namedtuple-like (form/tag/start/len)
        tokens = self._kiwi.tokenize(text)
        # 연속 명사 토큰 병합 (예: "온라인" + "취소" → "온라인 취소")
        buffer: list = []  # type: ignore[var-annotated]

        def flush():
            if not buffer:
                return
            surface = " ".join(t.form for t in buffer).strip()
            if len(surface) < 2:
                buffer.clear()
                return
            start = buffer[0].start
            end = buffer[-1].start + buffer[-1].len
            out.append(
                ExtractedEntity(
                    surface=surface,
                    canonical=_canonicalize(surface),
                    label="NN",
                    start=start,
                    end=end,
                )
            )
            buffer.clear()

        for tok in tokens:
            if tok.tag in self._NOUN_TAGS:
                buffer.append(tok)
            else:
                flush()
        flush()
        return out


# ── Backend 3: 정규식 (최후 수단) ─────────────────────────────────────


class RegexFallbackNER:
    """정규식 기반 단순 키워드 추출 — 최후 수단.

    KMS 표 데이터의 경우 `additional_keywords` (필수 키워드 사전) 을 inject 하는
    것이 일반적이라 spaCy/Kiwi 미설치 환경에서도 동작 가능.
    """

    # 한글 2글자 이상 + 영숫자 단어
    _KOREAN_WORD = re.compile(r"[가-힣]{2,}")
    _ALNUM_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]{1,}")

    @property
    def name(self) -> str:
        return "regex"

    def extract(self, text: str) -> list[ExtractedEntity]:
        if not text or not text.strip():
            return []
        out: list[ExtractedEntity] = []
        for m in self._KOREAN_WORD.finditer(text):
            surface = m.group(0)
            out.append(
                ExtractedEntity(
                    surface=surface,
                    canonical=_canonicalize(surface),
                    label="KO",
                    start=m.start(),
                    end=m.end(),
                )
            )
        for m in self._ALNUM_WORD.finditer(text):
            surface = m.group(0)
            out.append(
                ExtractedEntity(
                    surface=surface,
                    canonical=_canonicalize(surface),
                    label="EN",
                    start=m.start(),
                    end=m.end(),
                )
            )
        return out


# ── 정규화 / 동의어 통합 ─────────────────────────────────────────────


def _canonicalize(surface: str) -> str:
    """canonical form 생성 — strip + lowercase + 공백 단일화 + 동의어 매핑."""
    s = surface.strip().lower()
    s = re.sub(r"\s+", " ", s)
    return _SYNONYM_MAP.get(s, s)


# 한국어 KMS 도메인 동의어 사전 (확장 가능, V3 운영 중 학습 누적)
# canonical_form ← {variants}
_SYNONYM_MAP: dict[str, str] = {
    # 환불 관련
    "환불 처리": "환불",
    "환불처리": "환불",
    "리펀드": "환불",
    "환급": "환불",
    # 취소 관련
    "주문 취소": "취소",
    "주문취소": "취소",
    "캔슬": "취소",
    # 배송 관련
    "출고 완료": "출고완료",
    "출고완료": "출고완료",
    "배송 완료": "배송완료",
    "배송완료": "배송완료",
    # 결제수단
    "신용 카드": "신용카드",
    "신용카드": "신용카드",
    "체크 카드": "체크카드",
    "체크카드": "체크카드",
    "무통장 입금": "무통장",
    "무통장입금": "무통장",
    "계좌 이체": "계좌이체",
    "계좌이체": "계좌이체",
    # 기간
    "영업일 기준": "영업일",
    "영업일기준": "영업일",
    # 회원 관련
    "회원 탈퇴": "탈퇴",
    "회원탈퇴": "탈퇴",
    "회원 정보": "회원정보",
    "회원정보": "회원정보",
}


# ── 백엔드 자동 선택 ─────────────────────────────────────────────────

_BACKEND_CACHE: Optional[NERBackend] = None


def get_ner_backend() -> NERBackend:
    """환경변수 `QA_LINEAR_NER` 또는 자동 감지로 백엔드 선택."""
    global _BACKEND_CACHE
    if _BACKEND_CACHE is not None:
        return _BACKEND_CACHE

    requested = (os.environ.get(_ENV_BACKEND) or "auto").strip().lower()

    if requested in ("spacy", "auto"):
        try:
            backend = SpacyKoreanNER()
            logger.info("LinearRAG NER backend: %s", backend.name)
            _BACKEND_CACHE = backend
            return backend
        except RuntimeError as e:
            if requested == "spacy":
                raise
            logger.info("spaCy 미설치 — Kiwi 시도: %s", e)

    if requested in ("kiwi", "auto"):
        try:
            backend = KiwiNER()
            logger.info("LinearRAG NER backend: %s", backend.name)
            _BACKEND_CACHE = backend
            return backend
        except RuntimeError as e:
            if requested == "kiwi":
                raise
            logger.info("Kiwi 미설치 — regex fallback: %s", e)

    backend = RegexFallbackNER()
    logger.warning(
        "LinearRAG NER: regex fallback 사용 — 정확도 낮음. spaCy 또는 Kiwi 설치 권장."
    )
    _BACKEND_CACHE = backend
    return backend


def extract_entities(
    text: str,
    *,
    additional_keywords: Optional[list[str]] = None,
) -> list[ExtractedEntity]:
    """텍스트에서 entity 추출.

    Args:
        text: 추출 대상 텍스트.
        additional_keywords: KMS 표의 "필수 키워드" 컬럼 같이 사전에 알려진
            entity 후보. 정규식 매칭으로 surface 형태 보존.

    Returns:
        ExtractedEntity 리스트 (canonical 기준 중복 제거 안 함 — 호출부에서 처리).
    """
    backend = get_ner_backend()
    entities = backend.extract(text)

    # KMS 키워드 사전 매칭
    if additional_keywords:
        seen_canonicals = {e.canonical for e in entities}
        for kw in additional_keywords:
            kw_clean = kw.strip()
            if not kw_clean or len(kw_clean) < 2:
                continue
            canonical = _canonicalize(kw_clean)
            if canonical in seen_canonicals:
                continue
            # text 내 위치 찾기 (없어도 OK — start/end -1)
            idx = text.find(kw_clean)
            entities.append(
                ExtractedEntity(
                    surface=kw_clean,
                    canonical=canonical,
                    label="KW",
                    start=idx if idx >= 0 else -1,
                    end=(idx + len(kw_clean)) if idx >= 0 else -1,
                )
            )
            seen_canonicals.add(canonical)

    return entities


def reset_backend_cache() -> None:
    """테스트용 — 백엔드 캐시 초기화."""
    global _BACKEND_CACHE
    _BACKEND_CACHE = None
