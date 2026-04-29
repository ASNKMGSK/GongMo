// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { memo } from "react";

import {
  PERSONA_ORDER,
  PERSONA_STYLES,
  type Persona,
} from "@/lib/personas";
import type {
  DebateFinalEvent,
  ModeratorVerdictEvent,
  PersonaTurnEvent,
} from "@/lib/types";

export interface DebateRoundUI {
  round: number;
  max_rounds: number;
  // 페르소나별 최신 turn (round 당 최대 1회 등장)
  turns: Partial<Record<Persona, PersonaTurnEvent>>;
  /** 발언 도착 순서 (SSE 이벤트 수신 순서) — 채팅 UI 가 incoming 순으로 렌더 */
  turn_order?: Persona[];
  verdict: ModeratorVerdictEvent | null;
}

export interface DebateState {
  active: boolean;
  item_number: number | null;
  item_name: string | null;
  rounds: DebateRoundUI[];
  currentRound: number;
  maxRounds: number;
  final: DebateFinalEvent | null;
  startedAt: number | null;
}

export const INITIAL_DEBATE_STATE: DebateState = {
  active: false,
  item_number: null,
  item_name: null,
  rounds: [],
  currentRound: 0,
  maxRounds: 0,
  final: null,
  startedAt: null,
};

interface Props {
  state: DebateState;
}

function DebatePanelImpl({ state }: Props) {
  if (!state.active && !state.final) {
    return (
      <div className="debate-panel debate-panel--idle">
        <div className="debate-panel__placeholder">
          토론 대기 중 — spread 임계값을 넘는 항목이 나오면 실시간 토론이 여기에
          표시됩니다.
        </div>
      </div>
    );
  }

  const title = state.item_number
    ? `#${state.item_number}${state.item_name ? ` ${state.item_name}` : ""}`
    : "토론 진행";

  const activeRound =
    state.rounds.find((r) => r.round === state.currentRound) ??
    state.rounds[state.rounds.length - 1] ??
    null;

  return (
    <div className="debate-panel debate-panel--active">
      <header className="debate-panel__header">
        <div className="debate-panel__title">{title}</div>
        <div className="debate-panel__round">
          라운드{" "}
          <span className="debate-panel__round-current">
            {state.currentRound || "…"}
          </span>
          {state.maxRounds ? ` / ${state.maxRounds}` : ""}
        </div>
      </header>

      <section className="debate-panel__personas" aria-label="페르소나 발언">
        {PERSONA_ORDER.map((p) => {
          const turn = activeRound?.turns[p];
          return <PersonaCard key={p} persona={p} turn={turn} />;
        })}
      </section>

      <RoundTimeline rounds={state.rounds} max={state.maxRounds} />

      <ModeratorCell verdict={activeRound?.verdict ?? null} />

      {state.final && <FinalCard final={state.final} />}
    </div>
  );
}

export const DebatePanel = memo(DebatePanelImpl);

// ──────────────────────────────────────────────────────────────

interface PersonaCardProps {
  persona: Persona;
  turn?: PersonaTurnEvent;
}

const PersonaCardImpl = ({ persona, turn }: PersonaCardProps) => {
  const style = PERSONA_STYLES[persona];
  const hasTurn = !!turn;
  return (
    <article
      className={`persona-card persona-card--${persona}${hasTurn ? " persona-card--spoken" : ""}`}
      style={{
        background: style.bg,
        borderColor: style.border,
        color: style.color,
      }}
      aria-label={style.label}
    >
      <header className="persona-card__head">
        <span className="persona-card__label">{style.label}</span>
        <span className="persona-card__key">{persona}</span>
      </header>
      <div className="persona-card__score" aria-live="polite">
        {turn ? turn.score : "—"}
      </div>
      <div className="persona-card__argument">
        {turn ? turn.argument : "아직 발언 없음"}
      </div>
    </article>
  );
};

const PersonaCard = memo(PersonaCardImpl);

// ──────────────────────────────────────────────────────────────

interface ModeratorCellProps {
  verdict: ModeratorVerdictEvent | null;
}

const ModeratorCellImpl = ({ verdict }: ModeratorCellProps) => {
  if (!verdict) {
    return (
      <div className="moderator-cell moderator-cell--waiting">
        <span className="moderator-cell__label">모더레이터</span>
        <span className="moderator-cell__wait">판단 대기…</span>
      </div>
    );
  }
  const consensus = verdict.consensus;
  return (
    <div
      className={`moderator-cell ${consensus ? "moderator-cell--ok" : "moderator-cell--pending"}`}
    >
      <span className="moderator-cell__label">모더레이터 · R{verdict.round}</span>
      <span
        className={`consensus-badge ${consensus ? "consensus-badge--ok" : "consensus-badge--pending"}`}
      >
        {consensus ? "✓ 합의" : "△ 미합의"}
      </span>
      {verdict.score != null && (
        <span className="moderator-cell__score">중간 점수: {verdict.score}</span>
      )}
      {verdict.rationale && (
        <p className="moderator-cell__rationale">{verdict.rationale}</p>
      )}
    </div>
  );
};

const ModeratorCell = memo(ModeratorCellImpl);

// ──────────────────────────────────────────────────────────────

interface RoundTimelineProps {
  rounds: DebateRoundUI[];
  max: number;
}

const RoundTimelineImpl = ({ rounds, max }: RoundTimelineProps) => {
  const totalDots = Math.max(max || 1, rounds.length, 1);
  return (
    <div className="round-timeline" aria-label="라운드 진행">
      {Array.from({ length: totalDots }, (_, idx) => {
        const roundNo = idx + 1;
        const round = rounds.find((r) => r.round === roundNo);
        const verdict = round?.verdict;
        const state = verdict
          ? verdict.consensus
            ? "ok"
            : "pending"
          : round
            ? "active"
            : "future";
        return (
          <span
            key={roundNo}
            className={`round-dot round-dot--${state}`}
            title={`라운드 ${roundNo}`}
          >
            <span className="round-dot__num">{roundNo}</span>
          </span>
        );
      })}
    </div>
  );
};

const RoundTimeline = memo(RoundTimelineImpl);

// ──────────────────────────────────────────────────────────────

interface FinalCardProps {
  final: DebateFinalEvent;
}

const FinalCardImpl = ({ final }: FinalCardProps) => {
  const rule = final.converged ? "consensus" : "median_vote";
  return (
    <div className="debate-final">
      <div className="debate-final__head">
        <span className="debate-final__title">토론 종료</span>
        <span className={`debate-final__rule debate-final__rule--${rule}`}>
          {rule}
        </span>
      </div>
      <div className="debate-final__score">
        {final.final_score != null ? final.final_score : "—"}
      </div>
      <div className="debate-final__meta">
        라운드 {final.rounds_used} · {final.converged ? "합의" : "과반 투표"}
      </div>
      {final.rationale && (
        <p className="debate-final__rationale">{final.rationale}</p>
      )}
    </div>
  );
};

const FinalCard = memo(FinalCardImpl);

export default DebatePanel;
