# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""JSON parsing and repair utilities for LLM responses.

Extracts and repairs JSON from LLM output that may contain markdown fences,
<think> tags, trailing commas, unquoted keys, unclosed brackets, etc.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any


logger = logging.getLogger(__name__)


def _strip_think_tags(text: str) -> str:
    """Qwen 3의 <think>...</think> 사고 과정 태그를 제거하고 실제 응답만 반환."""
    stripped = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()
    return stripped if stripped else text


def _extract_text(content: Any) -> str:
    """LLM 응답 content 에서 문자열만 추출.

    ChatBedrockConverse 는 content 가 list[ContentBlock] 로 올 수 있다.
    SageMaker wrapper 는 항상 str.

    Bedrock content block 변형:
      - {"type": "text", "text": "..."}           (표준)
      - {"text": "..."}                           (type 생략)
      - {"type": "reasoning", "reasoning_content": {"text": "..."}}  (extended thinking — 무시)
      - {"type": "tool_use", ...}                 (도구 호출 — 무시)
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        reasoning_parts: list[str] = []
        skipped_types: list[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if "text" in block and isinstance(block["text"], str):
                    parts.append(block["text"])
                elif isinstance(block.get("content"), str):
                    parts.append(block["content"])
                elif block_type == "reasoning_content" and isinstance(block.get("reasoning_content"), dict):
                    rc_text = block["reasoning_content"].get("text", "")
                    if rc_text:
                        reasoning_parts.append(rc_text)
                else:
                    skipped_types.append(block_type or "unknown")
            elif isinstance(block, str):
                parts.append(block)
            else:
                parts.append(str(block))
        result = "".join(parts)
        if not result and reasoning_parts:
            logger.info(
                "_extract_text: only reasoning_content blocks found (no text block) — "
                "treating as empty to trigger rule fallback. reasoning_len=%d",
                sum(len(r) for r in reasoning_parts),
            )
        if not result:
            logger.warning(
                "_extract_text: empty text after extraction — blocks=%d, skipped_types=%s, sample=%r",
                len(content),
                skipped_types,
                content[:2],
            )
        return result
    return str(content)


def _repair_json(text: str) -> str:
    """Attempt to repair common LLM JSON mistakes.

    sLLM (Qwen3-8B) 은 중/대형 모델보다 JSON 포맷을 자주 틀린다.
    주요 실수 유형을 반복적으로 수정한다.
    """
    # 0. Single quotes → double quotes (Python-style dict → JSON)
    text = text.replace("'", '"')

    # 0b. Unquoted property names: `{ key:` → `{ "key":`
    text = re.sub(r"(?<=[{,])\s*([a-zA-Z_]\w*)\s*:", r' "\1":', text)

    # 1. Trailing comma before } or ]
    text = re.sub(r",(\s*[}\]])", r"\1", text)

    # 2. Missing comma between `}` and next object/array/key
    text = re.sub(r"(\})(\s*\n\s*)([\{\[\"])", r"\1,\2\3", text)

    # 3. Missing comma between `]` and next element
    text = re.sub(r"(\])(\s*\n\s*)([\{\[\"])", r"\1,\2\3", text)

    # 4. Missing comma between "string" and next key (only on newline boundary)
    text = re.sub(r'("[^"\\\n]*(?:\\.[^"\\\n]*)*")(\s*\n\s*)(")', r"\1,\2\3", text)

    # 5. Missing comma between primitive and next key
    text = re.sub(r'(\b(?:true|false|null)\b|-?\d+(?:\.\d+)?)(\s*\n\s*)(")', r"\1,\2\3", text)

    # 6. Same-line missing comma: `"value" "key"` → `"value", "key"`
    text = re.sub(r'(")(\s+)(")', r"\1,\2\3", text)

    # 7. Same-line missing comma: `5 "key"` / `true "key"`
    text = re.sub(r'(\b(?:true|false|null)\b|-?\d+(?:\.\d+)?)(\s+)(")', r"\1,\2\3", text)

    # 8. Same-line: `} {` or `} "key"` without newline
    text = re.sub(r"(\})(\s+)([\{\[\"])", r"\1,\2\3", text)

    # 9. Same-line: `] {` or `] "key"` without newline
    text = re.sub(r"(\])(\s+)([\{\[\"])", r"\1,\2\3", text)

    return text


def _close_unclosed_brackets(text: str) -> str:
    """Best-effort closure of unterminated brackets/braces (truncation 대비)."""
    in_str = False
    escape = False
    stack: list[str] = []
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()
    if in_str:
        text = text + '"'
    closer = {"{": "}", "[": "]"}
    for opener in reversed(stack):
        text += closer[opener]
    return text


def parse_llm_json(raw_text: Any) -> dict:
    """LLM 응답에서 JSON을 추출한다. 마크다운 코드 펜스 및 <think> 태그 처리.

    sLLM 이 잘못된 JSON 을 내놓을 수 있으므로 5단계 파싱 전략:
      1. 원본 그대로 파싱 (성공 시 즉시 반환)
      2. 공통 실수 자동 복구 후 재파싱 (missing comma, trailing comma 등)
      3. 미닫힘 괄호 보정 후 재파싱 (truncation 대비)
      4. 에러 위치에 콤마 직접 삽입
      5. Extra data — raw_decode로 첫 JSON 객체만 추출
      실패 시에만 원본 오류를 그대로 raise.

    raw_text 는 str 또는 list[ContentBlock] 수용 (Bedrock content block 대비).
    """
    original_type = type(raw_text).__name__
    if not isinstance(raw_text, str):
        raw_text = _extract_text(raw_text)
    text = _strip_think_tags(raw_text).strip()
    if not text:
        logger.warning(
            "parse_llm_json: empty LLM response — original_type=%s, raw_len=%d, raw_sample=%r",
            original_type,
            len(raw_text) if isinstance(raw_text, str) else -1,
            raw_text[:300] if isinstance(raw_text, str) else raw_text,
        )
        raise ValueError(f"parse_llm_json: empty LLM response (raw type={original_type})")
    if "```" in text:
        matches = re.findall(r"```(?:json|python)?\s*(.*?)\s*```", text, re.DOTALL)
        if matches:
            text = matches[0].strip()
        else:
            m = re.search(r"```(?:json|python)?\s*([\s\S]*)", text)
            if m:
                text = m.group(1).strip()
                text = re.sub(r"`+\s*$", "", text).strip()

    # 1차: 원본 시도
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        first_err = e

    # 2차: 공통 실수 복구
    repaired = _repair_json(text)
    try:
        result = json.loads(repaired)
        logger.warning("parse_llm_json: recovered via _repair_json (original error: %s)", first_err)
        return result
    except json.JSONDecodeError:
        pass

    # 3차: 괄호 닫기 보정 후 재시도
    closed = _close_unclosed_brackets(repaired)
    try:
        result = json.loads(closed)
        logger.warning("parse_llm_json: recovered via _close_unclosed_brackets (original error: %s)", first_err)
        return result
    except json.JSONDecodeError:
        pass

    # 4차: 에러 위치에 콤마 직접 삽입
    if "Expecting ',' delimiter" in str(first_err) and hasattr(first_err, "pos"):
        pos = first_err.pos
        patched = repaired[:pos] + "," + repaired[pos:]
        try:
            result = json.loads(patched)
            logger.warning("parse_llm_json: recovered via comma insertion at pos %d (original: %s)", pos, first_err)
            return result
        except json.JSONDecodeError:
            pass

    # 5차: "Extra data" — raw_decode 로 첫 JSON 객체만 추출
    if "Extra data" in str(first_err):
        try:
            decoder = json.JSONDecoder()
            obj, _end = decoder.raw_decode(text)
            logger.warning(
                "parse_llm_json: recovered via raw_decode — extracted first JSON, discarded trailing text (original: %s)",
                first_err,
            )
            return obj
        except json.JSONDecodeError:
            pass
        try:
            decoder = json.JSONDecoder()
            obj, _end = decoder.raw_decode(repaired)
            logger.warning("parse_llm_json: recovered via raw_decode (repaired) — extracted first JSON")
            return obj
        except json.JSONDecodeError:
            pass

    raise first_err
