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


# Cross-region inference profile 사용 — Sonnet 4.6 (`us.` 접두사 = US geo cross-region).
# ★ 2026-05-07: Sonnet 4 → 4.6 전환. TPM 200K → 6M (30배), 가격 동일.
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
DEFAULT_REGION = "us-east-1"
DEFAULT_TEMPERATURE = 0.3


def build_llm_config(bedrock_model_id: str | None = None) -> dict[str, Any]:
    """Bedrock LLMConfig 를 AG2 가 기대하는 dict 포맷으로 빌드.

    AG2 v0.9.7 은 ``LLMConfig(api_type="bedrock", ...)`` 또는 동등한 dict 를 수용한다.
    dict 반환이 더 호환성이 넓어 테스트/fallback 에 유리.

    ★ 2026-05-07: 모델 우선순위 — 인자 (request override) → BEDROCK_MODEL_ID env → DEFAULT_MODEL_ID.
    프론트 드롭다운 선택값을 토론에도 동일하게 적용하기 위함.
    """
    # ★ 2026-05-07: 페르소나 응답 길이 캡 (env QA_DEBATE_MAX_TOKENS, 기본 800).
    # 토론 발언이 장문화되어 토큰/시간 비대해지는 것 방지. JSON {score, reasoning, deductions[], evidence[]}
    # 정도면 600~800 으로 충분. 0/음수면 미지정 (모델 기본).
    _max_tokens_raw = os.getenv("QA_DEBATE_MAX_TOKENS", "800").strip()
    try:
        _max_tokens = int(_max_tokens_raw)
    except (TypeError, ValueError):
        _max_tokens = 800
    resolved_model = bedrock_model_id or os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    cfg_entry: dict[str, Any] = {
        "api_type": "bedrock",
        "model": resolved_model,
        "aws_region": os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", DEFAULT_REGION)),
    }
    if _max_tokens > 0:
        cfg_entry["max_tokens"] = _max_tokens
    return {
        "config_list": [cfg_entry],
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
                    # ★ 2026-04-30 S7 fix: 라운드 합의 즉시 마킹 (1턴 over-shoot 방지).
                    # 마지막 persona 발화 후 hook 이 실행되는 시점에 vbr[cur_round] 가 완성됨.
                    # _is_termination 이 호출 순서 race 로 다음 라운드 첫 persona 까지 발화시키는
                    # 케이스를 막기 위해 hook 안에서 명시 플래그로 마킹.
                    if len(round_votes) >= len(PERSONA_ORDER):
                        if len(set(round_votes.values())) == 1:
                            round_tracker["should_terminate_consensus"] = True
                            logger.info(
                                "[AG2][item=#%s] 라운드 %d 합의 감지 (전원 %s) — 종료 플래그 마킹",
                                req.item_number,
                                cur_round,
                                next(iter(set(round_votes.values()))),
                            )
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
    # ★ 2026-05-07: req.bedrock_model_id (프론트 드롭다운) 를 AG2 LLMConfig 에 주입.
    # 페르소나 + Manager LLM 모두 같은 모델 사용 → 사용자 선택 모델로 통일.
    llm_config = build_llm_config(getattr(req, "bedrock_model_id", None))

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

    # max_round = persona 수 × 최대 라운드 수 (★ 2026-04-30: fallback 여유 제거).
    # 이전 공식 `_max_rounds * persona_count + persona_count` 는 1라운드 정책이어도 합의 미도달 시
    # +1라운드 더 진행하여 사실상 2라운드까지 도는 문제 발생. 사용자 정책상 strict 1라운드 정책이므로
    # 합의 못 하면 라운드 1 후 즉시 종료 → 판사 결정 단계로 이행.
    persona_count = max(1, len(PERSONA_ORDER))
    _max_rounds = max(1, int(getattr(req, "max_rounds", DEFAULT_MAX_ROUNDS) or DEFAULT_MAX_ROUNDS))
    max_round = _max_rounds * persona_count  # strict cap — 사용자 설정 라운드 그대로

    # speaker_selection_method — env 로 토글 가능.
    # ★ 2026-05-07: Sonnet 4.6 (TPM 6M) 전환으로 default round_robin → auto 변경.
    #   auto       : Manager 가 매 턴 다음 발언자 LLM 선택. 자유 토론 자연스러움 ↑.
    #   round_robin: persona 순서 고정. 토큰 ~50% 절감하지만 페르소나가 서로 반박/동의 자연성 ↓.
    selection_method = os.getenv("QA_DEBATE_SPEAKER_SELECTION", "auto").strip().lower()
    if selection_method not in ("round_robin", "auto", "random", "manual"):
        selection_method = "auto"

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
        # ★ 2026-04-30 S7 fix: hook 이 미리 마킹한 플래그 우선 검사.
        # hook 안에서 라운드 완성 시점에 should_terminate_consensus=True 로 마킹되므로
        # is_termination_msg 호출 순서와 무관하게 결정적으로 종료.
        if round_tracker.get("should_terminate_consensus"):
            return True
        content = (msg.get("content") or "") if isinstance(msg, dict) else ""
        upper = content.upper()
        # ★ 2026-05-07: 신/구 두 경로 모두 지원
        #   신 경로: JSON 의 final_vote: true 필드 (debate_rules.md 6번 룰)
        #   구 경로: reasoning 본문에 "VOTE_FINAL"/"CONSENSUS" 토큰 (LLM 이 따르지 않을 때)
        if "CONSENSUS" in upper or "VOTE_FINAL" in upper:
            return True
        try:
            import json as _json, re as _re
            mb = _re.search(r"\{[\s\S]*\}", content)
            if mb:
                obj = _json.loads(mb.group(0))
                if isinstance(obj, dict) and obj.get("final_vote") is True:
                    return True
        except Exception:
            pass
        # 라운드별 투표 맵: {round: {persona: score}} — fallback (구 경로, 호환성 유지)
        vbr: dict[int, dict[str, int]] = round_tracker.get("votes_by_round") or {}
        if not vbr:
            return False
        latest_round = max(vbr.keys())
        votes = vbr[latest_round]
        if len(votes) < persona_count:
            return False
        scores_in_round = set(votes.values())
        if len(scores_in_round) == 1:
            logger.info(
                "debate: round-consensus fallback 감지 — 라운드 %d 전원 동일 점수 %s, 종료",
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
