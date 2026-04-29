# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""AG2 (autogen) GroupChat 팀 빌더 — 페르소나 자유 토론.

설계 포인트:
 - ``speaker_selection_method="auto"`` — AI 가 맥락 보고 다음 발언자 자동 선택. 라운드 없는 연속 대화.
 - ``allow_repeat_speaker=True`` — 같은 페르소나가 연속 발언 가능 (반박 집중).
 - ``select_speaker_message_template`` — QA 토론 도메인 맞춤 발언자 선택 프롬프트.
 - ``is_termination_msg`` — 발언에 ``CONSENSUS`` 또는 ``VOTE_FINAL`` 포함 시 즉시 종료.
 - ``LLMConfig(api_type="bedrock", ...)`` — AG2 v0.9.7 Bedrock 네이티브 백엔드.
 - 각 agent 발언 직전 ``process_message_before_send`` 훅으로 SSE 콜백 전달.

AG2 미설치 환경에서는 import 실패 가능 — caller 가 반드시 try/except 로 감싸야 한다.
"""

from __future__ import annotations

import autogen
import logging
import os
from autogen import AssistantAgent, GroupChat, GroupChatManager
from collections.abc import Callable
from typing import Any
from v2.debate.personas import MODERATOR_SYSTEM, PERSONA_ORDER, build_persona_system_prompt
from v2.debate.schemas import DEFAULT_MAX_ROUNDS, DebateRequest


logger = logging.getLogger(__name__)


# Cross-region inference profile 사용 — Sonnet 4 는 on-demand throughput 미지원,
# `us.` 접두사가 붙은 inference profile 만 호출 가능 (CLAUDE.md 기본값과 일치).
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"
DEFAULT_REGION = "us-east-1"
DEFAULT_TEMPERATURE = 0.3


def build_llm_config() -> dict[str, Any]:
    """Bedrock LLMConfig 를 AG2 가 기대하는 dict 포맷으로 빌드.

    AG2 v0.9.7 은 ``LLMConfig(api_type="bedrock", ...)`` 또는 동등한 dict 를 수용한다.
    dict 반환이 더 호환성이 넓어 테스트/fallback 에 유리.
    """
    return {
        "config_list": [
            {
                "api_type": "bedrock",
                "model": os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
                "aws_region": os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", DEFAULT_REGION)),
            }
        ],
        "cache_seed": None,
        "temperature": float(os.getenv("QA_DEBATE_TEMPERATURE", str(DEFAULT_TEMPERATURE))),
    }


EventCallback = Callable[[str, dict[str, Any]], None]


def _attach_event_hook(
    agent: AssistantAgent,
    on_event: EventCallback,
    *,
    req: DebateRequest | None = None,
    discussion_id: str = "",
    node_id: str = "",
    round_tracker: dict[str, Any] | None = None,
) -> None:
    """``process_message_before_send`` 훅으로 매 발언 직전 on_event 호출.

    ★ v3 실시간 스트리밍: AG2 GroupChat 은 `initiate_chat()` 이 블록킹되며 내부에서
    모든 턴을 동기적으로 돌림. 따라서 `_reconstruct_rounds` 에서 이벤트를 뽑으면
    전체 토론이 끝난 후에야 프론트에 도달 (수분 지연). 훅이 실시간으로 emit 하면
    DiscussionModal 이 persona 발언을 즉시 표시.

    emit 이벤트 (프론트 호환 — persona_speaking + persona_message + vote_cast):
      - persona_speaking : 발언 직전 (누가 말하고 있는지)
      - persona_message  : 발언 완료 (content + score/reasoning 파싱)
      - vote_cast        : 점수 투표 (persona_message 와 동시)
    """
    # 지연 import — _reconstruct_rounds 와 동일한 파서를 쓰기 위해 run_debate 의 유틸 활용
    from v2.debate.run_debate import _parse_persona_json

    def _hook(sender: Any, message: str | dict[str, Any], recipient: Any, silent: bool) -> str | dict[str, Any]:
        try:
            if isinstance(message, dict):
                content = str(message.get("content", ""))
            else:
                content = str(message)
            sender_name = (getattr(sender, "name", None) or agent.name or "").lower()
            recipient_name = getattr(recipient, "name", None)

            # ── 상세 로그: AG2 GroupChat 내 모든 turn 을 터미널에 출력 ──
            item_no = req.item_number if req else "?"
            preview = content[:160].replace("\n", " ")
            logger.info(
                "[AG2][item=#%s] %s → %s (%d chars): %s%s",
                item_no,
                sender_name,
                recipient_name,
                len(content),
                preview,
                "…" if len(content) > 160 else "",
            )

            # ── round_tracker 진단: persona scoring turn 일 때만 round 증가 ──
            # raw_turn 이벤트에 round 를 실어야 _structure_rounds 가 추측 알고리즘 없이
            # 정확한 라운드로 RoundRecord 를 구성할 수 있다 (사용자 보고: 추측 알고리즘이
            # 같은 persona 가 한 라운드 안에서 두 번 발화하는 throttle 재시도 케이스에서
            # 4+ 버킷으로 잘못 쪼개는 버그).
            cur_round_for_raw = 1
            is_scoring_turn = False
            parsed_for_round = None
            if sender_name in PERSONA_ORDER and req is not None:
                parsed_for_round = _parse_persona_json(content, allowed_steps=list(req.allowed_steps))
                is_scoring_turn = (
                    parsed_for_round is not None
                    and isinstance(parsed_for_round.get("score"), (int, float))
                )
                if is_scoring_turn and round_tracker is not None:
                    seen = round_tracker.setdefault("seen", set())
                    if sender_name in seen:
                        round_tracker["round"] = int(round_tracker.get("round", 1)) + 1
                        seen.clear()
                    seen.add(sender_name)
                    cur_round_for_raw = int(round_tracker.get("round", 1))
                    # max_rounds cap — 트래커가 over-count 해도 사용자 설정 이상으로 안 올라가게
                    cur_round_for_raw = min(
                        cur_round_for_raw,
                        max(1, int(getattr(req, "max_rounds", DEFAULT_MAX_ROUNDS) or DEFAULT_MAX_ROUNDS)),
                    )
                elif round_tracker is not None:
                    cur_round_for_raw = int(round_tracker.get("round", 1))

            # 1) legacy raw_turn — round 를 같이 실어보내 _structure_rounds 가 그대로 사용
            on_event(
                "raw_turn",
                {
                    "agent_name": sender_name,
                    "content": content,
                    "recipient": recipient_name,
                    "silent": bool(silent),
                    "round": cur_round_for_raw,
                    "is_scoring": is_scoring_turn,
                },
            )

            # 2) ★ 실시간 persona_speaking / persona_message / vote_cast — 프론트 DiscussionModal 이 즉시 표시
            if sender_name in PERSONA_ORDER and req is not None:
                parsed = parsed_for_round  # 위에서 한 번만 파싱
                has_score = is_scoring_turn

                if not has_score:
                    # 초기 task 메시지 또는 파싱 실패 turn — persona 응답 아님. skip.
                    logger.info(
                        "[AG2][item=#%s] %s → %s: persona 응답 아님 (JSON score 없음) — persona 이벤트 skip",
                        req.item_number,
                        sender_name,
                        recipient_name,
                    )
                    return message

                cur_round = cur_round_for_raw

                score_val = int(parsed["score"])  # type: ignore[arg-type]
                # votes_by_round 에 기록 — _is_termination 이 "현재 라운드 전원 동점" 감지에 사용.
                # recent_votes 는 legacy 용으로 남겨두지만 종료 판정에는 사용하지 않음 (사용자 피드백:
                # "라운드 1 마지막 2명 + 라운드 2 첫 1명 일치로 조기 종료" 하는 크로스-라운드 합의 금지).
                if round_tracker is not None:
                    vbr: dict[int, dict[str, int]] = round_tracker.setdefault("votes_by_round", {})
                    round_votes = vbr.setdefault(cur_round, {})
                    round_votes[sender_name] = score_val
                    # legacy recent_votes — 로그/디버깅 용 (종료 판정에는 미사용)
                    votes_list: list = round_tracker.setdefault("recent_votes", [])
                    votes_list.append((sender_name, score_val))
                    keep = max(6, (len(PERSONA_ORDER) * 2))
                    if len(votes_list) > keep:
                        del votes_list[:-keep]
                reasoning = str(parsed.get("reasoning") or content[:500])
                rebuttal = parsed.get("rebuttal") or None

                on_event(
                    "persona_speaking",
                    {
                        "discussion_id": discussion_id,
                        "node_id": node_id,
                        "item_number": req.item_number,
                        "round": cur_round,
                        "persona_id": sender_name,
                    },
                )
                on_event(
                    "persona_message",
                    {
                        "discussion_id": discussion_id,
                        "node_id": node_id,
                        "item_number": req.item_number,
                        "round": cur_round,
                        "persona_id": sender_name,
                        "message": reasoning,
                        "score_proposal": float(score_val),
                        "rebuttal": rebuttal,
                        "evidence_refs": [],
                    },
                )
                on_event(
                    "vote_cast",
                    {
                        "discussion_id": discussion_id,
                        "node_id": node_id,
                        "item_number": req.item_number,
                        "round": cur_round,
                        "persona_id": sender_name,
                        "score": float(score_val),
                    },
                )
        except Exception:  # pragma: no cover — SSE 콜백 오류가 토론 자체를 막으면 안 됨
            logger.exception("debate event hook failed for agent=%s", agent.name)
        return message

    agent.register_hook("process_message_before_send", _hook)


_SPEAKER_SELECT_TEMPLATE = (
    "당신은 QA 평가 토론의 진행자입니다. 참여자: {roles}\n"
    "대화 맥락을 보고 다음 발언자를 {agentlist} 중에서 골라 이름만 답하세요.\n"
    "규칙:\n"
    "- 강한 주장이 나오면 반대 의견을 가진 사람에게 반박 기회를 주세요.\n"
    "- 이미 동의한 사람은 건너뛰어도 됩니다.\n"
    "- 쟁점이 없으면 빠르게 마무리하세요.\n"
    "- 개인정��� 항목이면 컴플라이언스 관점을 우선하세요.\n"
)


def build_debate_team(
    req: DebateRequest,
    on_event: EventCallback,
    *,
    discussion_id: str = "",
    node_id: str = "",
) -> tuple[GroupChatManager, list[AssistantAgent]]:
    """페르소나 + GroupChatManager 빌드 (auto 모드, 라운드 없는 연속 대화).

    Args:
        discussion_id, node_id : 훅에서 실시간 emit 하는 persona_speaking/message/vote_cast
                                 이벤트에 이 식별자를 실어야 프론트 DiscussionModal 이 매칭.

    Returns
    -------
    (manager, personas)
        personas 는 PERSONA_ORDER 순서.
    """
    llm_config = build_llm_config()

    personas: list[AssistantAgent] = []
    # round_tracker 를 persona 간 공유 — 같은 persona 두 번째 발화 시 round 증가
    # ★ recent_votes: 최근 persona turn 의 (agent_name, score) 기록.
    #   같은 persona 수만큼 누적되고 전부 동일 점수이면 consensus → 강제 종료.
    round_tracker: dict[str, Any] = {
        "round": 1,
        "seen": set(),
        "recent_votes": [],  # list[(persona_name, score)] — 최근 N턴만 유지
    }
    for name in PERSONA_ORDER:
        agent = AssistantAgent(
            name=name, system_message=build_persona_system_prompt(name, req.allowed_steps), llm_config=llm_config
        )
        _attach_event_hook(
            agent,
            on_event,
            req=req,
            discussion_id=discussion_id,
            node_id=node_id,
            round_tracker=round_tracker,
        )
        personas.append(agent)

    # max_round = persona 수 × 최대 라운드 수 + 약간의 여유.
    # req.max_rounds 기본 2 (사용자 정책 2026-04-27), personas 3명 → 6턴. 여유 +3 → 9턴 상한.
    # 즉, 사용자가 설정한 max_rounds 를 초과해서 polling 돌지 않도록 strict cap.
    persona_count = max(1, len(PERSONA_ORDER))
    _max_rounds = max(1, int(getattr(req, "max_rounds", DEFAULT_MAX_ROUNDS) or DEFAULT_MAX_ROUNDS))
    max_round = _max_rounds * persona_count + persona_count  # + 1라운드 여유 (fallback)

    # speaker_selection_method — env 로 토글 가능. 기본 round_robin (Bedrock TPM 절감용).
    #   round_robin: persona 순서대로 발언. Manager hidden speaker-selection LLM 호출 없음 → 토큰 ~50% 절감.
    #   auto       : Manager 가 매 턴 다음 발언자 LLM 선택. 자유 토론 자연스러움 ↑, 토큰/Throttling 위험 ↑.
    selection_method = os.getenv("QA_DEBATE_SPEAKER_SELECTION", "round_robin").strip().lower()
    if selection_method not in ("round_robin", "auto", "random", "manual"):
        selection_method = "round_robin"

    groupchat_kwargs: dict[str, Any] = dict(
        agents=[*personas],
        messages=[],
        max_round=max_round,
        speaker_selection_method=selection_method,
        allow_repeat_speaker=False if selection_method == "round_robin" else True,
    )
    # auto 일 때만 speaker-select LLM 프롬프트 템플릿 + verbose 전달 (round_robin 은 LLM 안 씀)
    if selection_method == "auto":
        groupchat_kwargs["select_speaker_message_template"] = _SPEAKER_SELECT_TEMPLATE
        groupchat_kwargs["select_speaker_auto_verbose"] = (
            os.getenv("QA_DEBATE_VERBOSE", "").lower() in ("1", "true")
        )

    groupchat = GroupChat(**groupchat_kwargs)
    logger.info(
        "debate GroupChat: speaker_selection=%s max_round=%d (Bedrock TPM 최적화)",
        selection_method,
        max_round,
    )

    # ★ 합의 감지: "라운드 경계 기준" — 현재 라운드에 모든 persona 가 투표 완료 &&
    #   그 라운드의 점수가 전원 일치일 때만 종료.
    #   (이전 구현은 크로스-라운드 슬라이딩 윈도우였으나, 사용자 피드백으로 금지됨:
    #    "라운드 다 진행하고 서로 점수가 맞으면 그때 종료" — 라운드1 후반 + 라운드2 초반
    #    일치로 라운드2 중간에 끊으면 UI 상 부분 라운드로 어색함.)
    def _is_termination(msg: dict[str, Any]) -> bool:
        content = (msg.get("content") or "") if isinstance(msg, dict) else ""
        upper = content.upper()
        if "CONSENSUS" in upper or "VOTE_FINAL" in upper:
            return True
        # 라운드별 투표 맵: {round: {persona: score}}
        vbr: dict[int, dict[str, int]] = round_tracker.get("votes_by_round") or {}
        if not vbr:
            return False
        # 가장 최근 라운드만 본다 — 더 오래된 라운드는 이미 미합의로 진행된 라운드.
        latest_round = max(vbr.keys())
        votes = vbr[latest_round]
        if len(votes) < persona_count:
            return False  # 라운드 아직 진행 중 — 경계 아님
        scores_in_round = set(votes.values())
        if len(scores_in_round) == 1:
            logger.info(
                "debate: round-consensus 감지 — 라운드 %d 전원 동일 점수 %s, 종료",
                latest_round,
                next(iter(scores_in_round)),
            )
            return True
        return False

    manager = GroupChatManager(groupchat=groupchat, llm_config=llm_config, is_termination_msg=_is_termination)

    logger.info(
        "debate team built: item=%s max_turns=%d mode=auto allowed_steps=%s",
        req.item_number,
        max_round,
        req.allowed_steps,
    )
    return manager, personas


__all__ = ["autogen", "build_debate_team", "build_llm_config", "EventCallback"]
