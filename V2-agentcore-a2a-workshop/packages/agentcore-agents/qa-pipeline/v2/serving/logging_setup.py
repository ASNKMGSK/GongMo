# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""공용 로깅 설정 — `main_v2.py` 뿐 아니라 `uvicorn v2.serving.server_v2:app` 직접 기동 경로도 커버.

import 만으로 부수효과: root logger 설정 + 외부 시끄러운 라이브러리 suppress +
botocore 이벤트 훅 등록 (Bedrock 호출을 INFO 로 찍음).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any


_CONFIGURED = False


def configure() -> None:
    """Idempotent — 여러 번 호출돼도 1회만 설정."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(threadName)s|%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )

    # 과하게 시끄러운 외부 라이브러리 suppress (bedrock 훅은 우리가 직접 INFO 로 찍는다)
    for noisy in (
        "boto3",
        "botocore",
        "urllib3",
        "httpx",
        "httpcore",
        "asyncio",
        "anyio",
        "autogen",
        "opensearch",
        "langchain",
        "langchain_aws",
        "watchfiles",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # 우리 앱 로거
    for ours in ("v2", "nodes", "graph", "__main__"):
        logging.getLogger(ours).setLevel(level)

    # QA_LOG_DEBUG=1 → 우리 앱만 DEBUG 로 승격
    if os.environ.get("QA_LOG_DEBUG", "").lower() in ("1", "true", "yes"):
        for ours in ("v2", "nodes", "graph", "__main__"):
            logging.getLogger(ours).setLevel(logging.DEBUG)

    _install_bedrock_hook()


# ---------------------------------------------------------------------------
# Bedrock 호출 훅 — botocore 이벤트 시스템으로 요청/응답 관찰
# ---------------------------------------------------------------------------


_bedrock_logger = logging.getLogger("bedrock")
_bedrock_call_counter: dict[str, int] = {"n": 0}
_bedrock_timings: dict[int, float] = {}


def _install_bedrock_hook() -> None:
    """모든 bedrock-runtime 호출을 콘솔에 찍는다.

    botocore.Session 의 이벤트 시스템을 사용 — LangChain / AG2 / 직접 boto3 호출
    모두 동일 Session 을 공유하므로 한 군데 훅으로 전체 커버.

    훅 포인트:
      - before-call.bedrock-runtime.* : 요청 직전 (operation/model/payload 크기)
      - after-call.bedrock-runtime.*  : 응답 도착 (elapsed + 응답 크기/status)
    """
    try:
        import boto3
        session = boto3.DEFAULT_SESSION or boto3.Session()
        # boto3.DEFAULT_SESSION 이 아직 없으면 만들어서 고정 — 이후 boto3.client 가 이걸 쓴다.
        if boto3.DEFAULT_SESSION is None:
            boto3.setup_default_session()
            session = boto3.DEFAULT_SESSION
        events = session.events
    except Exception as exc:  # pragma: no cover — boto3 미설치
        _bedrock_logger.warning("bedrock hook 설치 실패 — boto3 import error: %s", exc)
        return

    def _before_call(params: dict[str, Any], model: Any, **_kwargs: Any) -> None:
        try:
            op = getattr(model, "name", "?")
            model_id = params.get("modelId") or params.get("ModelId") or "?"
            # invoke_model 은 body(bytes|str), converse 는 messages 배열
            body = params.get("body") or params.get("Body")
            msgs = params.get("messages") or params.get("Messages")
            if isinstance(body, (bytes, bytearray)):
                body_size = len(body)
                try:
                    parsed = json.loads(body)
                    max_tokens = parsed.get("max_tokens") or parsed.get("max_gen_len")
                    temperature = parsed.get("temperature")
                except Exception:
                    max_tokens = temperature = None
            elif isinstance(body, str):
                body_size = len(body.encode("utf-8"))
                max_tokens = temperature = None
            else:
                body_size = 0
                max_tokens = temperature = None
            if msgs and isinstance(msgs, list):
                msgs_count = len(msgs)
                msgs_chars = sum(
                    len(str(m)) if not isinstance(m, dict) else len(json.dumps(m, ensure_ascii=False))
                    for m in msgs
                )
            else:
                msgs_count = 0
                msgs_chars = 0

            _bedrock_call_counter["n"] += 1
            call_id = _bedrock_call_counter["n"]
            _bedrock_timings[call_id] = time.perf_counter()
            # call_id 를 params 에 심어 after-call 에서 매칭
            params["_qa_call_id"] = call_id
            _bedrock_logger.info(
                "🟣 BEDROCK CALL #%d → %s · model=%s · body=%dB · msgs=%d/%dch · temp=%s max_tokens=%s",
                call_id,
                op,
                model_id,
                body_size,
                msgs_count,
                msgs_chars,
                temperature,
                max_tokens,
            )
        except Exception:
            _bedrock_logger.exception("bedrock before-call 훅 실패")

    def _after_call(
        http_response: Any, parsed: Any, model: Any, context: Any = None, **_kwargs: Any
    ) -> None:
        try:
            op = getattr(model, "name", "?")
            status = getattr(http_response, "status_code", "?")
            # context 에 params 가 들어오는 경우도 있음 — 타이밍 lookup
            call_id = None
            if isinstance(parsed, dict):
                # Nothing here — parsed is response, not request
                pass
            # fallback — latest timing
            call_id = max(_bedrock_timings.keys()) if _bedrock_timings else None
            elapsed = (
                time.perf_counter() - _bedrock_timings.pop(call_id, time.perf_counter())
                if call_id is not None
                else 0.0
            )
            # 응답 크기 추정
            resp_size = 0
            text_preview = ""
            if isinstance(parsed, dict):
                body = parsed.get("body")
                if hasattr(body, "read"):
                    # StreamingBody — 읽지 말 것 (caller 가 읽어야 함). ContentLength 로 대체.
                    resp_size = int(parsed.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("content-length", 0) or 0)
                elif isinstance(body, (bytes, bytearray)):
                    resp_size = len(body)
                # converse 응답의 output.message 미리보기
                out = parsed.get("output") or {}
                msg = (out.get("message") or {}) if isinstance(out, dict) else {}
                parts = msg.get("content") or []
                if isinstance(parts, list):
                    for p in parts:
                        if isinstance(p, dict) and "text" in p:
                            text_preview = str(p["text"])[:100].replace("\n", " ")
                            break
                usage = parsed.get("usage") or parsed.get("ResponseMetadata", {}).get("usage")
            else:
                usage = None

            _bedrock_logger.info(
                "🟢 BEDROCK RESP #%s ← %s · HTTP %s · %.2fs · body=%dB · usage=%s%s",
                call_id if call_id is not None else "?",
                op,
                status,
                elapsed,
                resp_size,
                usage or "n/a",
                f" · preview='{text_preview}…'" if text_preview else "",
            )
        except Exception:
            _bedrock_logger.exception("bedrock after-call 훅 실패")

    # bedrock-runtime 의 모든 operation 에 등록
    events.register("before-call.bedrock-runtime", _before_call)
    events.register("after-call.bedrock-runtime", _after_call)
    # bedrock (관리용 API) 도 참고용
    events.register("before-call.bedrock", _before_call)
    events.register("after-call.bedrock", _after_call)

    _bedrock_logger.info("✅ Bedrock 호출 훅 설치 완료 — 모든 bedrock-runtime 요청을 로깅")
