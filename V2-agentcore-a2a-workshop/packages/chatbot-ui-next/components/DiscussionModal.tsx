// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  PERSONA_ORDER,
  PERSONA_STYLES,
  type Persona,
} from "@/lib/personas";
import {
  NODE_DEFS,
  NODE_TO_DEBATE_ITEMS,
} from "@/lib/pipeline";

// 항목별 만점 — v2/contracts/rubric.py::ALLOWED_STEPS 의 첫 원소(내림차순 최대값) mirror.
// 토론은 "항목 단위" 이므로 노드(카테고리) 합계가 아니라 해당 item 의 배점을 써야 함.
// 2026-04-21 rubric 기준:
//   인사 예절 #1/#2 = 5점 / 경청 및 소통 #4/#5 = 5점 / 언어 표현 #6/#7 = 5점
//   니즈 파악 #8/#9 = 5점 / 설명력 및 전달력 #10 = 10점 #11 = 5점
//   적극성 #12/#13/#14 = 5점 / 업무 정확도 #15 = 15점 #16 = 5점
//   개인정보 보호 #17/#18 = 5점
const ITEM_MAX_SCORES: Record<number, number> = {
  1: 5, 2: 5,
  4: 5, 5: 5,
  6: 5, 7: 5,
  8: 5, 9: 5,
  10: 10, 11: 5,
  12: 5, 13: 5, 14: 5,
  15: 15, 16: 5,
  17: 5, 18: 5,
};

// 노드별 카테고리 합계 — 최후 fallback (항목별 maxScore 를 알 수 없는 예외 케이스용).
// 정상 흐름에서는 ITEM_MAX_SCORES 로 해결되어야 함.
const NODE_MAX_SCORES: Record<string, number> = {
  greeting: 10,       // #1+#2 = 5+5
  listening_comm: 10, // #4+#5 = 5+5
  language: 10,       // #6+#7 = 5+5
  needs: 10,          // #8+#9 = 5+5
  explanation: 15,    // #10+#11 = 10+5
  proactiveness: 15,  // #12+#13+#14 = 5+5+5
  work_accuracy: 20,  // #15+#16 = 15+5
  privacy: 10,        // #17+#18 = 5+5
};
import type {
  DebateFinalEvent,
  ModeratorVerdictEvent,
  PersonaTurnEvent,
} from "@/lib/types";

import type { DebateRoundUI, DebateState } from "./DebatePanel";

/**
 * DiscussionModal — 노드의 "💬 토론 시작" 버튼 클릭 시 열리는 인터랙티브 패널.
 * 채팅 로그 형식으로 라운드별 페르소나 발언 + 모더레이터 판정 + 최종 합의 결과를 시각화.
 */

export type DiscussionStartMode = "auto" | "manual";

export interface ActiveDebateRef {
  nodeId: string;
  label: string;
  phase: "before" | "running" | "done";
  speakingPersona?: Persona | null;
  round?: number;
  maxRounds?: number;
}

export interface DiscussionModalProps {
  open: boolean;
  nodeId: string | null;
  state: DebateState;
  /** ★ item 별 DebateState — 한 노드에 여러 item 있을 때 탭으로 선택해서 각 토론 확인. */
  stateByItem?: Record<number, DebateState>;
  onClose: () => void;
  onStart?: (nodeId: string, mode: DiscussionStartMode) => void;
  onNextRound?: (nodeId: string) => void;
  onAbort?: (nodeId: string) => void;
  activeDebates?: ActiveDebateRef[];
  onSelectNode?: (nodeId: string) => void;
  speakingPersona?: Persona | null;
}

function DiscussionModalImpl({
  open,
  nodeId,
  state: fallbackState,
  stateByItem,
  onClose,
  onStart,
  onNextRound,
  onAbort,
  activeDebates,
  onSelectNode,
  speakingPersona,
}: DiscussionModalProps) {
  const [mode, setMode] = useState<DiscussionStartMode>("auto");
  // 선택된 item_number — 노드의 debateable items 중 어떤 토론을 보여줄지
  const [selectedItem, setSelectedItem] = useState<number | null>(null);

  // 현재 노드의 item 리스트 (토론 대상만)
  const nodeItems = useMemo<number[]>(() => {
    if (!nodeId) return [];
    return NODE_TO_DEBATE_ITEMS[nodeId] ?? [];
  }, [nodeId]);

  // stateByItem 기준 "토론 데이터가 있는" item 만 선택지로
  const itemsWithDebate = useMemo<number[]>(() => {
    if (!stateByItem) return nodeItems;
    return nodeItems.filter((it) => {
      const s = stateByItem[it];
      return s && (s.active || s.final || s.rounds.length > 0);
    });
  }, [nodeItems, stateByItem]);

  // 기본 선택: itemsWithDebate 첫 번째, 없으면 fallbackState.item_number
  useEffect(() => {
    if (!open) return;
    if (selectedItem != null && itemsWithDebate.includes(selectedItem)) return;
    const initial = itemsWithDebate[0] ?? fallbackState.item_number ?? null;
    setSelectedItem(initial);
  }, [open, nodeId, itemsWithDebate, fallbackState.item_number, selectedItem]);

  // 실제 렌더에 사용할 state — 선택된 item 있으면 그 state, 없으면 fallback
  const state: DebateState =
    (selectedItem != null && stateByItem?.[selectedItem]) || fallbackState;
  const dialogRef = useRef<HTMLDivElement>(null);
  const chatLogRef = useRef<HTMLDivElement>(null);

  // 새 메시지 도착 시 스크롤 하단 자동 고정
  useEffect(() => {
    if (!open) return;
    const el = chatLogRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [open, state.rounds, state.currentRound, state.final, speakingPersona]);

  // ESC 닫기
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const handleStart = useCallback(() => {
    if (!nodeId || !onStart) return;
    onStart(nodeId, mode);
  }, [nodeId, mode, onStart]);

  const handleNext = useCallback(() => {
    if (!nodeId || !onNextRound) return;
    onNextRound(nodeId);
  }, [nodeId, onNextRound]);

  const handleAbort = useCallback(() => {
    if (!nodeId || !onAbort) return;
    if (window.confirm("진행 중인 토론을 중단하시겠습니까?")) {
      onAbort(nodeId);
    }
  }, [nodeId, onAbort]);

  // 각 페르소나의 "마지막 라운드 점수" — 합의/투표 내역을 최종 블록에서 보여주기 위함
  const personaFinalScores = useMemo((): Partial<Record<Persona, PersonaTurnEvent>> => {
    const result: Partial<Record<Persona, PersonaTurnEvent>> = {};
    for (const r of state.rounds) {
      for (const p of PERSONA_ORDER) {
        const t = r.turns[p];
        if (t) result[p] = t;
      }
    }
    return result;
  }, [state.rounds]);

  if (!open || !nodeId) return null;

  const def = NODE_DEFS[nodeId];
  const nodeLabel = def?.label ?? nodeId;
  // per-item max — 토론은 항목 단위이므로 선택된 item 의 배점이 기준.
  // 우선순위: ITEM_MAX_SCORES[selectedItem] > final.max_score > turns[].max_score > NODE fallback.
  const perItemMaxFromTable =
    selectedItem != null ? ITEM_MAX_SCORES[selectedItem] ?? null : null;
  const perItemMaxFromFinal =
    typeof (state.final as unknown as { max_score?: number } | null)?.max_score === "number"
      ? (state.final as unknown as { max_score: number }).max_score
      : null;
  // ★ 버그 수정: r.turns 는 Partial<Record<Persona, PersonaTurnEvent>> (dict) 이므로
  //   flatMap(r => r.turns) 로는 turn 하나도 못 뽑음. Object.values 로 풀어야 함.
  const perItemMaxFromTurn =
    state.rounds
      .flatMap((r) => Object.values(r.turns))
      .map((t) => (t as unknown as { max_score?: number } | undefined)?.max_score)
      .find((m): m is number => typeof m === "number" && m > 0) ?? null;
  const maxScore =
    perItemMaxFromTable ??
    perItemMaxFromFinal ??
    perItemMaxFromTurn ??
    NODE_MAX_SCORES[nodeId] ??
    null;

  const phase: "before" | "running" | "done" =
    state.final ? "done" : state.active ? "running" : "before";

  const activeRound: DebateRoundUI | null =
    state.rounds.find((r) => r.round === state.currentRound) ??
    state.rounds[state.rounds.length - 1] ??
    null;

  const lastVerdict = activeRound?.verdict ?? null;
  const canNextRound =
    phase === "running" &&
    mode === "manual" &&
    lastVerdict !== null &&
    !lastVerdict.consensus &&
    state.currentRound < state.maxRounds;

  return (
    <div
      className="discussion-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="discussion-modal-title"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        className="discussion-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="discussion-modal__header">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="discussion-modal__eyebrow">
              {phase === "before" && "토론 시작 대기"}
              {phase === "running" && (
                <>
                  토론 진행 중
                  <span className="discussion-modal__live-dot" aria-hidden="true" />
                </>
              )}
              {phase === "done" && "토론 완료"}
            </div>
            <h2 id="discussion-modal-title" className="discussion-modal__title">
              {nodeLabel}
            </h2>
            <div className="discussion-modal__sub">
              {selectedItem != null
                ? `평가 항목 #${selectedItem}`
                : nodeItems.length > 0
                  ? `평가 항목 ${nodeItems.map((n) => `#${n}`).join(", ")}`
                  : "평가 항목"}
              {" · 3명 페르소나 합의"}
              {maxScore ? ` · 만점 ${maxScore}점` : ""}
            </div>
          </div>
          <button
            type="button"
            className="discussion-modal__close"
            onClick={onClose}
            aria-label="닫기"
          >
            ✕
          </button>
        </header>

        {/* 병렬 토론 탭 스트립 — 다른 노드 토론으로 이동 */}
        {activeDebates && activeDebates.length > 1 && (
          <nav className="discussion-modal__tabs" aria-label="진행 중인 다른 토론">
            {activeDebates.map((d) => {
              const active = d.nodeId === nodeId;
              return (
                <button
                  key={d.nodeId}
                  type="button"
                  className={`discussion-tab${active ? " discussion-tab--active" : ""} discussion-tab--${d.phase}`}
                  onClick={() => onSelectNode?.(d.nodeId)}
                  disabled={active}
                  title={`${d.label} (${d.phase})`}
                >
                  <span className={`discussion-tab__dot discussion-tab__dot--${d.phase}`} />
                  <span className="discussion-tab__label">{d.label}</span>
                  {d.round != null && d.phase === "running" && (
                    <span className="discussion-tab__round">
                      R{d.round}/{d.maxRounds ?? "?"}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        )}

        {/* ★ 노드 내 item 별 토론 탭 — 한 노드에 여러 item 있는 경우 (예: proactiveness #12/#13/#14) */}
        {itemsWithDebate.length > 1 && (
          <nav
            className="discussion-modal__tabs"
            aria-label="평가 항목별 토론 선택"
            style={{ background: "var(--surface-muted)" }}
          >
            {itemsWithDebate.map((itemNo) => {
              const s = stateByItem?.[itemNo];
              const itemPhase: "before" | "running" | "done" =
                s?.final ? "done" : s?.active ? "running" : "before";
              const active = itemNo === selectedItem;
              return (
                <button
                  key={itemNo}
                  type="button"
                  className={`discussion-tab${active ? " discussion-tab--active" : ""} discussion-tab--${itemPhase}`}
                  onClick={() => setSelectedItem(itemNo)}
                  disabled={active}
                  title={`평가 항목 #${itemNo} (${itemPhase})`}
                >
                  <span
                    className={`discussion-tab__dot discussion-tab__dot--${itemPhase}`}
                  />
                  <span className="discussion-tab__label">#{itemNo}</span>
                  {s?.currentRound != null && itemPhase === "running" && (
                    <span className="discussion-tab__round">
                      R{s.currentRound}/{s.maxRounds ?? "?"}
                    </span>
                  )}
                  {itemPhase === "done" && s?.final?.final_score != null && (
                    <span className="discussion-tab__round">{s.final.final_score}점</span>
                  )}
                </button>
              );
            })}
          </nav>
        )}

        {/* phase: before */}
        {phase === "before" && (
          <section className="discussion-modal__starter">
            <div className="discussion-modal__starter-text">
              3명의 페르소나(<strong>품격 · 정확성 · 고객경험</strong>)가 라운드별로
              점수와 근거를 발언합니다. 모더레이터가 합의 여부를 판정하고, 미합의 시
              다음 라운드로 진행합니다.
            </div>
            <div className="discussion-modal__mode-toggle" role="radiogroup" aria-label="진행 방식">
              <label className={`mode-pill ${mode === "auto" ? "mode-pill--active" : ""}`}>
                <input
                  type="radio"
                  name="discussion-mode"
                  value="auto"
                  checked={mode === "auto"}
                  onChange={() => setMode("auto")}
                />
                자동 진행
                <span className="mode-pill__hint">전체 라운드를 끊김 없이</span>
              </label>
              <label className={`mode-pill ${mode === "manual" ? "mode-pill--active" : ""}`}>
                <input
                  type="radio"
                  name="discussion-mode"
                  value="manual"
                  checked={mode === "manual"}
                  onChange={() => setMode("manual")}
                />
                수동 진행
                <span className="mode-pill__hint">라운드마다 사용자 확인</span>
              </label>
            </div>
            <button
              type="button"
              className="discussion-modal__start-btn"
              onClick={handleStart}
              disabled={!onStart}
            >
              ▶ 토론 시작
            </button>
            {!onStart && (
              <div className="discussion-modal__warn">
                ⚠ 백엔드가 자동으로 토론을 시작합니다 (auto_start=true). 이 다이얼로그는
                실시간 진행만 보여줍니다.
              </div>
            )}
          </section>
        )}

        {/* phase: running / done — 채팅 로그 */}
        {phase !== "before" && (
          <section className="discussion-modal__body">
            <div className="discussion-modal__round-bar">
              <span className="round-bar__label">라운드 진행</span>
              <RoundDots
                rounds={state.rounds}
                max={state.maxRounds}
                current={state.currentRound}
              />
            </div>

            <div className="chat-log" ref={chatLogRef}>
              {state.rounds.length === 0 && (
                <div className="chat-log__empty">
                  토론 시작 중… 첫 번째 페르소나가 발언을 준비하고 있습니다.
                </div>
              )}

              {state.rounds.map((round) => (
                <RoundBlock
                  key={round.round}
                  round={round}
                  maxScore={maxScore}
                  isCurrent={round.round === state.currentRound}
                  speakingPersona={
                    round.round === state.currentRound ? speakingPersona : null
                  }
                />
              ))}
            </div>

            {state.final && (
              <FinalBlock final={state.final} personaScores={personaFinalScores} maxScore={maxScore} />
            )}

            <div className="discussion-modal__controls">
              {phase === "running" && mode === "manual" && (
                <button
                  type="button"
                  className="control-btn control-btn--primary"
                  onClick={handleNext}
                  disabled={!canNextRound || !onNextRound}
                >
                  다음 라운드 진행 →
                </button>
              )}
              {phase === "running" && (
                <button
                  type="button"
                  className="control-btn control-btn--ghost"
                  onClick={handleAbort}
                  disabled={!onAbort}
                >
                  중단
                </button>
              )}
              {phase === "done" && (
                <button
                  type="button"
                  className="control-btn control-btn--primary"
                  onClick={onClose}
                >
                  닫기
                </button>
              )}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

export const DiscussionModal = memo(DiscussionModalImpl);

// ──────────────────────────────────────────────────────────────
// RoundBlock — 하나의 라운드 = 페르소나 메시지 + 모더레이터 판정 메시지
// ──────────────────────────────────────────────────────────────

interface RoundBlockProps {
  round: DebateRoundUI;
  maxScore: number | null;
  isCurrent: boolean;
  speakingPersona?: Persona | null;
}

const RoundBlockImpl = ({ round, maxScore, isCurrent, speakingPersona }: RoundBlockProps) => {
  // 발언 도착 순서 (SSE incoming order) — turn_order 가 있으면 그 순서 사용,
  // 없으면 PERSONA_ORDER 로 fallback (이전 데이터 호환).
  // 아직 발언하지 않은 페르소나는 PERSONA_ORDER 의 잔여 순서로 typing/skip 표시.
  const orderedPersonas: Persona[] = (() => {
    const seen = new Set<Persona>(round.turn_order || []);
    const remaining = PERSONA_ORDER.filter((p) => !seen.has(p));
    return [...(round.turn_order || []), ...remaining];
  })();

  return (
    <div className={`chat-round${isCurrent ? " chat-round--current" : ""}`}>
      <div className="chat-round__divider">
        <span className="chat-round__label">라운드 {round.round}</span>
        <span className="chat-round__line" aria-hidden="true" />
      </div>
      {orderedPersonas.map((p) => {
        const turn = round.turns[p];
        if (turn) {
          return (
            <ChatMessage
              key={`${round.round}-${p}`}
              persona={p}
              turn={turn}
              maxScore={maxScore}
            />
          );
        }
        if (isCurrent && speakingPersona === p) {
          return <TypingMessage key={`${round.round}-${p}-typing`} persona={p} />;
        }
        return null;
      })}
      {round.verdict && <ModeratorMessage verdict={round.verdict} />}
    </div>
  );
};

const RoundBlock = memo(RoundBlockImpl);

// ──────────────────────────────────────────────────────────────
// ChatMessage — 채팅 버블 형태의 페르소나 발언
// ──────────────────────────────────────────────────────────────

interface ChatMessageProps {
  persona: Persona;
  turn: PersonaTurnEvent;
  maxScore: number | null;
}

const ChatMessageImpl = ({ persona, turn, maxScore }: ChatMessageProps) => {
  const style = PERSONA_STYLES[persona];
  // 신규 메시지 강조 — 첫 마운트 후 2.6초 동안 slide-in + glow.
  // turn.argument 가 바뀌면 같은 persona 라도 신규 라운드 메시지이므로 다시 fresh 강조.
  const [fresh, setFresh] = useState(true);
  useEffect(() => {
    setFresh(true);
    const t = window.setTimeout(() => setFresh(false), 2600);
    return () => window.clearTimeout(t);
  }, [turn.argument, turn.score]);

  return (
    <div
      className={`chat-msg chat-msg--${persona}${fresh ? " chat-msg--fresh" : ""}`}
      style={
        fresh
          ? ({
              // CSS 변수로 persona 색을 주입 → globals.css 의 @keyframes 에서 활용.
              ["--persona-color" as string]: style.color,
              ["--persona-border" as string]: style.border,
            } as React.CSSProperties)
          : undefined
      }
    >
      <div
        className="chat-msg__avatar"
        style={{ background: style.bg, color: style.color, borderColor: style.border }}
        aria-hidden="true"
      >
        {avatarFor(persona)}
      </div>
      <div className="chat-msg__body">
        <header className="chat-msg__head">
          <span className="chat-msg__name" style={{ color: style.color }}>
            {style.label}
          </span>
          <span className="chat-msg__role">{persona}</span>
          {fresh && (
            <span
              className="chat-msg__new-badge"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 3,
                marginLeft: 4,
                padding: "1px 7px",
                fontSize: 9,
                fontWeight: 700,
                letterSpacing: "0.05em",
                color: "#ffffff",
                background: style.color,
                borderRadius: 9999,
                textTransform: "uppercase",
                // 2가지 애니메이션 동시 적용 — pulse(깜빡임, 무한) + lifecycle(enter/hold/fade-out, 1회).
                // lifecycle 이 2.6s 끝에서 opacity 0 으로 빠져 클래스 제거 시 끊김 없음.
                animation:
                  "freshBadgePulse 1.2s ease-in-out infinite, freshBadgeLifecycle 2.6s ease-out 1 both",
              }}
            >
              <span
                style={{
                  width: 4,
                  height: 4,
                  borderRadius: "50%",
                  background: "#ffffff",
                }}
                aria-hidden="true"
              />
              NEW
            </span>
          )}
          <span
            className="chat-msg__score-chip"
            style={{ borderColor: style.border, color: style.color, background: style.bg }}
          >
            {turn.score}
            {maxScore ? <span className="chat-msg__score-max"> / {maxScore}</span> : null}
          </span>
        </header>
        <div
          className="chat-msg__bubble"
          style={{ borderColor: style.border }}
        >
          {turn.argument || "—"}
        </div>
      </div>
    </div>
  );
};

const ChatMessage = memo(ChatMessageImpl);

// ──────────────────────────────────────────────────────────────
// TypingMessage — 발언 중 타이핑 인디케이터
// ──────────────────────────────────────────────────────────────

interface TypingMessageProps {
  persona: Persona;
}

const TypingMessageImpl = ({ persona }: TypingMessageProps) => {
  const style = PERSONA_STYLES[persona];
  return (
    <div className={`chat-msg chat-msg--${persona} chat-msg--typing`}>
      <div
        className="chat-msg__avatar chat-msg__avatar--pulse"
        style={{ background: style.bg, color: style.color, borderColor: style.border }}
        aria-hidden="true"
      >
        {avatarFor(persona)}
      </div>
      <div className="chat-msg__body">
        <header className="chat-msg__head">
          <span className="chat-msg__name" style={{ color: style.color }}>
            {style.label}
          </span>
          <span className="chat-msg__role">{persona}</span>
          <span className="chat-msg__status">발언 중…</span>
        </header>
        <div className="chat-msg__bubble chat-msg__bubble--typing" style={{ borderColor: style.border }}>
          <span className="chat-typing-dot" />
          <span className="chat-typing-dot" />
          <span className="chat-typing-dot" />
        </div>
      </div>
    </div>
  );
};

const TypingMessage = memo(TypingMessageImpl);

function avatarFor(p: Persona): string {
  switch (p) {
    case "strict":
      return "🧑‍⚖️";
    case "neutral":
      return "🤝";
    case "loose":
      return "🌿";
    default:
      return "💬";
  }
}

// ──────────────────────────────────────────────────────────────
// ModeratorMessage — 모더레이터 판정 (채팅 메시지 형식)
// ──────────────────────────────────────────────────────────────

interface ModeratorMessageProps {
  verdict: ModeratorVerdictEvent;
}

const ModeratorMessageImpl = ({ verdict }: ModeratorMessageProps) => {
  const consensus = verdict.consensus;
  return (
    <div className={`chat-msg chat-msg--moderator ${consensus ? "chat-msg--consensus" : "chat-msg--pending"}`}>
      <div className="chat-msg__avatar chat-msg__avatar--moderator" aria-hidden="true">
        ⚖️
      </div>
      <div className="chat-msg__body">
        <header className="chat-msg__head">
          <span className="chat-msg__name chat-msg__name--moderator">모더레이터</span>
          <span className="chat-msg__role">라운드 {verdict.round} 판정</span>
          <span
            className={`chat-msg__verdict-chip ${consensus ? "chat-msg__verdict-chip--ok" : "chat-msg__verdict-chip--pending"}`}
          >
            {consensus ? "✓ 합의 도달" : "△ 미합의"}
          </span>
          {verdict.score != null && (
            <span className="chat-msg__score-chip chat-msg__score-chip--moderator">
              중간점수 {verdict.score}
            </span>
          )}
        </header>
        <div className="chat-msg__bubble chat-msg__bubble--moderator">
          {verdict.rationale || (consensus ? "페르소나 간 점수 일치로 합의." : "다음 라운드로 진행합니다.")}
        </div>
      </div>
    </div>
  );
};

const ModeratorMessage = memo(ModeratorMessageImpl);

// ──────────────────────────────────────────────────────────────
// RoundDots
// ──────────────────────────────────────────────────────────────

interface RoundDotsProps {
  rounds: DebateRoundUI[];
  max: number;
  current: number;
}

const RoundDotsImpl = ({ rounds, max, current }: RoundDotsProps) => {
  // max 가 명시되면 그 값을 dot 개수로 사용 (rounds.length 가 max 를 넘어도 max 에 cap).
  // max 가 0/undefined 일 때만 rounds.length fallback.
  const total = max && max > 0 ? max : Math.max(rounds.length, 1);
  return (
    <div className="round-dots" aria-label="라운드 타임라인">
      {Array.from({ length: total }, (_, idx) => {
        const n = idx + 1;
        const round = rounds.find((r) => r.round === n);
        const verdict = round?.verdict;
        const isCurrent = n === current;
        const cls = verdict
          ? verdict.consensus
            ? "round-dots__item--ok"
            : "round-dots__item--pending"
          : round
            ? "round-dots__item--active"
            : "round-dots__item--future";
        return (
          <span
            key={n}
            className={`round-dots__item ${cls}${isCurrent ? " round-dots__item--current" : ""}`}
            title={`라운드 ${n}`}
          >
            {n}
          </span>
        );
      })}
    </div>
  );
};

const RoundDots = memo(RoundDotsImpl);

// ──────────────────────────────────────────────────────────────
// FinalBlock — 최종 결과 (합의 여부 · 최종 점수 · 페르소나별 최종 점수 · 근거)
// ──────────────────────────────────────────────────────────────

interface FinalBlockProps {
  final: DebateFinalEvent;
  personaScores: Partial<Record<Persona, PersonaTurnEvent>>;
  maxScore: number | null;
}

const FinalBlockImpl = ({ final, personaScores, maxScore }: FinalBlockProps) => {
  // 점수 분포 — 어떤 점수에 몇 명 몰렸는지 bar chart 로 시각화
  const scoreDistribution = useMemo(() => {
    const counts: Record<number, Persona[]> = {};
    PERSONA_ORDER.forEach((p) => {
      const s = personaScores[p]?.score;
      if (s == null) return;
      if (!counts[s]) counts[s] = [];
      counts[s].push(p);
    });
    return Object.entries(counts)
      .map(([score, personas]) => ({ score: Number(score), personas }))
      .sort((a, b) => a.score - b.score);
  }, [personaScores]);

  const totalVotes = PERSONA_ORDER.filter((p) => personaScores[p]?.score != null).length;

  // 결정 방식 분기 — backend merge_rule 우선, 없으면 페르소나 점수 일치 여부로 판정.
  //   judge_post_debate : 판사 LLM 이 transcript 보고 최종 결정 (메인 본문 = 판사 결정) — 정책상 기본 경로
  //   consensus         : 페르소나 3명 만장일치 (판사 호출 실패 시 fallback)
  //   median_vote        : 합의 미달 → median 폴백 (판사 호출 실패 시)
  //   fallback_median    : AG2 토론 실행 실패 시 폴백
  // 사용자 정책 (2026-04-27): 토론 결과는 무조건 판사가 결정. "투표"/"과반" 어휘는
  // 더 이상 정책상 존재하지 않으며, 점수가 갈리면 판사 결정 또는 median 폴백 둘 중 하나.
  const mergeRule = (final as { merge_rule?: string }).merge_rule;
  const judgeFinal = final as {
    judge_score?: number | null;
    judge_reasoning?: string | null;
    judge_failure_reason?: string | null;
    judge_deductions?: Array<{ reason: string; points: number }>;
    judge_evidence?: Array<{ speaker: string; quote: string }>;
  };
  // 2026-04-27 v2: 판사 호출 성공 시 메인 본문 = 판사 결정. merge_rule="judge_post_debate" 신호.
  const judgeDecided = mergeRule === "judge_post_debate";
  const judgeAvailable =
    judgeFinal.judge_score != null && !!judgeFinal.judge_reasoning;
  const isFallback = mergeRule === "fallback_median";
  const allMatch =
    totalVotes > 0 &&
    final.final_score != null &&
    PERSONA_ORDER.every((p) => {
      const s = personaScores[p]?.score;
      return s != null && s === final.final_score;
    });
  // 판사 결정 케이스에서는 페르소나 점수 비교 의미 없음 — converged 표시는 판사 우선.
  const converged = judgeDecided ? true : allMatch && !isFallback;
  const backendClaimedConverged = !!final.converged;
  const hasDiscrepancy =
    !judgeDecided && backendClaimedConverged && !allMatch && !isFallback;

  // 다수결 (과반) 점수 찾기 — 투표 시 '채택' 배지 기준
  const majorityScore = useMemo(() => {
    if (converged) return final.final_score;
    const sorted = [...scoreDistribution].sort(
      (a, b) => b.personas.length - a.personas.length,
    );
    return sorted[0]?.score ?? final.final_score;
  }, [converged, scoreDistribution, final.final_score]);

  return (
    <div className={`final-verdict final-verdict--${judgeDecided ? "judge" : converged ? "consensus" : "vote"}`}>
      {/* ── 최상단 강조 배너 ── */}
      <div
        className={`final-verdict__banner final-verdict__banner--${judgeDecided ? "judge" : converged ? "consensus" : "vote"}`}
        role="status"
      >
        <span className="final-verdict__banner-icon" aria-hidden="true">
          {isFallback ? "⚠" : judgeDecided ? "🎭" : converged ? "🤝" : "⚠"}
        </span>
        <div className="final-verdict__banner-text">
          <div className="final-verdict__banner-title">
            {isFallback
              ? "AG2 토론 실패 — 폴백 점수 채택"
              : judgeDecided
                ? "🎭 판사 결정"
                : converged
                  ? "만장일치 합의"
                  : "판사 호출 실패 — median 폴백"}
          </div>
          <div className="final-verdict__banner-sub">
            {isFallback
              ? "AG2 토론 실행 중 오류 발생 — 초기 페르소나 점수의 median 으로 폴백했습니다."
              : judgeDecided
                ? `${final.rounds_used}라운드 토론 transcript 를 판사 LLM 이 검토하여 최종 점수를 결정했습니다.`
                : converged
                  ? "3명의 페르소나가 모두 동일한 점수를 선택했습니다."
                  : `${final.rounds_used}라운드 토론 후에도 의견이 갈려 판사 LLM 이 결정해야 했으나 호출 실패 — 페르소나 점수 median 으로 폴백했습니다.`}
          </div>
        </div>
        <span
          className={`final-verdict__banner-pill final-verdict__banner-pill--${judgeDecided ? "judge" : converged ? "consensus" : "vote"}`}
        >
          {isFallback ? "✕ FALLBACK" : judgeDecided ? "🎭 JUDGE" : converged ? "✓ CONSENSUS" : "✕ JUDGE FAIL"}
        </span>
      </div>

      {/* 백엔드가 converged=true 로 왔지만 실제 점수가 갈린 경우 — 사용자 혼란 방지용 노트 */}
      {hasDiscrepancy && (
        <div className="final-verdict__discrepancy">
          ⚠ 백엔드 merge_rule 은 <b>수렴</b> 으로 표기됐으나 페르소나별 점수가 서로 다릅니다.
          최종값은 median 폴백 기준이며, 표기는 실제 점수 일치 여부로 판정했습니다.
        </div>
      )}

      {/* ── 최종 점수 패널 ── */}
      <div className="final-verdict__score-panel">
        <div className="final-verdict__score-block">
          <div className="final-verdict__score-label">최종 점수</div>
          <div className="final-verdict__score-row">
            <span className="final-verdict__score-value" aria-live="polite">
              {final.final_score != null ? final.final_score : "—"}
            </span>
            {maxScore && (
              <span className="final-verdict__score-max">/ {maxScore}</span>
            )}
          </div>
        </div>
        <div className="final-verdict__meta">
          <div className="final-verdict__meta-item">
            <span className="final-verdict__meta-label">소요 라운드</span>
            <span className="final-verdict__meta-value">{final.rounds_used}</span>
          </div>
          <div className="final-verdict__meta-item">
            <span className="final-verdict__meta-label">결정 방식</span>
            <span className="final-verdict__meta-value">
              {isFallback
                ? "AG2 실패 (median 폴백)"
                : judgeDecided
                  ? "판사 결정"
                  : converged
                    ? "만장일치"
                    : "median 폴백"}
            </span>
          </div>
          <div className="final-verdict__meta-item">
            <span className="final-verdict__meta-label">참여 페르소나</span>
            <span className="final-verdict__meta-value">{totalVotes} / 3</span>
          </div>
        </div>
      </div>

      {/* ── 페르소나별 최종 점수 + 근거 ── */}
      <div className="final-verdict__section">
        <div className="final-verdict__section-label">
          <span className="final-verdict__section-dot" aria-hidden="true" />
          페르소나별 최종 점수 · 근거
        </div>
        <div className="final-verdict__votes-list">
          {PERSONA_ORDER.map((p) => {
            const turn = personaScores[p];
            const style = PERSONA_STYLES[p];
            const matchesFinal =
              turn?.score != null &&
              final.final_score != null &&
              turn.score === final.final_score;
            const matchesMajority =
              turn?.score != null &&
              majorityScore != null &&
              turn.score === majorityScore;
            const isAdopted = matchesFinal || matchesMajority;
            return (
              <div
                key={p}
                className={`final-vote-card${isAdopted ? " final-vote-card--match" : " final-vote-card--outvoted"}`}
                style={{
                  borderColor: isAdopted ? style.color : style.border,
                  background: style.bg,
                }}
              >
                <div className="final-vote-card__head">
                  <span className="final-vote-card__avatar" aria-hidden="true">
                    {avatarFor(p)}
                  </span>
                  <span className="final-vote-card__label" style={{ color: style.color }}>
                    {style.label}
                  </span>
                  <span className="final-vote-card__role">{p}</span>
                  {isAdopted ? (
                    <span
                      className="final-vote-card__match-chip"
                      style={{ background: style.color }}
                      title={converged ? "만장일치 점수" : "최종 결정과 일치"}
                    >
                      {converged ? "✓ 일치" : "✓ 채택"}
                    </span>
                  ) : (
                    <span className="final-vote-card__outvoted-chip" title="최종 결정에서 선택되지 않음">
                      ✗ 비채택
                    </span>
                  )}
                </div>
                <div className="final-vote-card__score" style={{ color: style.color }}>
                  {turn ? turn.score : "—"}
                  {maxScore && turn ? (
                    <span className="final-vote-card__score-max"> / {maxScore}</span>
                  ) : null}
                </div>
                {turn?.argument && (
                  <details className="final-vote-card__arg-details">
                    <summary className="final-vote-card__arg-summary">근거 보기</summary>
                    <div className="final-vote-card__arg-full">{turn.argument}</div>
                  </details>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── 점수 분포 (의견 갈렸을 때만) ── */}
      {!converged && scoreDistribution.length > 1 && (
        <div className="final-verdict__section">
          <div className="final-verdict__section-label">
            <span className="final-verdict__section-dot" aria-hidden="true" />
            점수 분포
          </div>
          <div className="final-verdict__distribution">
            {scoreDistribution.map(({ score, personas }) => {
              const isWinning = score === final.final_score;
              const widthPct = (personas.length / 3) * 100;
              return (
                <div
                  key={score}
                  className={`score-dist-row${isWinning ? " score-dist-row--winning" : ""}`}
                >
                  <div className="score-dist-row__score">
                    {score}
                    {maxScore ? <span className="score-dist-row__max">/{maxScore}</span> : null}
                  </div>
                  <div className="score-dist-row__bar-wrap">
                    <div
                      className={`score-dist-row__bar${isWinning ? " score-dist-row__bar--winning" : ""}`}
                      style={{ width: `${widthPct}%` }}
                    />
                    <div className="score-dist-row__personas">
                      {personas.map((p) => {
                        const s = PERSONA_STYLES[p];
                        return (
                          <span
                            key={p}
                            className="score-dist-row__chip"
                            style={{ background: s.bg, color: s.color, borderColor: s.border }}
                            title={s.label}
                          >
                            {avatarFor(p)} {s.label}
                          </span>
                        );
                      })}
                      <span className="score-dist-row__count">{personas.length}표</span>
                      {isWinning && (
                        <span className="score-dist-row__winner-badge">★ 선택</span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── 결정 사유 (consensus / median / fallback 라벨 텍스트) ──
          판사 결정 케이스에서는 아래 "🎭 판사 결정" 카드가 동일 내용 + 감점/근거 더 풍부하게
          표시하므로 중복 방지 위해 숨김. 판사 미사용 케이스 (만장일치 / median 폴백 등) 만 표시. */}
      {final.rationale && !judgeAvailable && (
        <div className="final-verdict__section">
          <div className="final-verdict__section-label">
            <span className="final-verdict__section-dot" aria-hidden="true" />
            📊 결정 사유
          </div>
          <p className="final-verdict__rationale">{final.rationale}</p>
        </div>
      )}

      {/* ── 🎭 판사 결정 — judge LLM 호출 성공 시만 표시. 실패 시 사유 표기. ── */}
      {judgeAvailable ? (
        <div className="final-verdict__section">
          <div className="final-verdict__section-label">
            <span className="final-verdict__section-dot" aria-hidden="true" />
            🎭 판사 결정
            <span style={{ marginLeft: 6, fontSize: 11, color: "#7c5cff" }}>
              (점수 {judgeFinal.judge_score}
              {maxScore ? `/${maxScore}` : ""})
            </span>
          </div>
          <p className="final-verdict__rationale">{judgeFinal.judge_reasoning}</p>
          {Array.isArray(judgeFinal.judge_deductions) &&
            judgeFinal.judge_deductions.length > 0 && (
              <ul style={{ marginTop: 8, paddingLeft: 18, color: "#b94a4a", fontSize: 12 }}>
                {judgeFinal.judge_deductions.slice(0, 5).map((d, i) => (
                  <li key={i}>
                    <b>-{d.points}점</b> · {d.reason}
                  </li>
                ))}
              </ul>
            )}
          {Array.isArray(judgeFinal.judge_evidence) &&
            judgeFinal.judge_evidence.length > 0 && (
              <ul
                style={{
                  marginTop: 8,
                  paddingLeft: 18,
                  color: "#444",
                  fontSize: 12,
                  fontStyle: "italic",
                }}
              >
                {judgeFinal.judge_evidence.slice(0, 5).map((e, i) => (
                  <li key={i}>
                    [{e.speaker}] &ldquo;{e.quote}&rdquo;
                  </li>
                ))}
              </ul>
            )}
        </div>
      ) : judgeFinal.judge_failure_reason ? (
        <div className="final-verdict__section">
          <div className="final-verdict__section-label">
            <span className="final-verdict__section-dot" aria-hidden="true" />
            🎭 판사 호출 실패
          </div>
          <p className="final-verdict__rationale" style={{ color: "#b94a4a" }}>
            {judgeFinal.judge_failure_reason}
          </p>
        </div>
      ) : null}
    </div>
  );
};

const FinalBlock = memo(FinalBlockImpl);

export default DiscussionModal;
