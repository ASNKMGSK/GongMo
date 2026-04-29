// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { memo } from "react";

import { shapes, type ShapeName } from "./shapes";

/**
 * EdgeTraveler — ReactFlow UI animated-svg-edge 패턴
 * (https://reactflow.dev/ui/components/animated-svg-edge).
 *
 * shapes 레코드의 컴포넌트가 `<animateMotion>` 으로 path 를 따라 이동.
 * 여러 packet 이 phaseOffset 만큼 시차를 두고 반복 (chain 효과 — `begin` 활용).
 *
 * SMIL 의 <animateMotion> 은 React 재렌더 시 reset 되지 않으므로 (브라우저 SVG 엔진이
 * 직접 시간 관리) 별도 rAF 없이도 부드럽게 이어진다.
 */

export interface EdgeTravelerProps {
  path: string;
  stroke: string;
  /** 1 cycle 시간 (ms). 기본 2400ms */
  duration?: number;
  /** 시작 phase offset (0~1). 여러 엣지 동시 활성화 시 분산용 — 첫 packet 의 begin delay 로 사용. */
  phaseOffset?: number;
  /** 체인 packet 개수. 기본 3 — begin 을 dur/N 간격으로 분산. */
  chainCount?: number;
  /** packet 모양. shapes 레코드의 키. 기본 "box" */
  shape?: ShapeName;
  /** [legacy] 더 이상 사용되지 않음 — shape 컴포넌트가 자체 크기 결정 */
  radius?: number;
}

function EdgeTravelerImpl({
  path,
  stroke,
  duration = 2400,
  phaseOffset = 0,
  chainCount = 3,
  shape = "box",
}: EdgeTravelerProps) {
  const Shape = shapes[shape] ?? shapes.box;
  const durSec = duration / 1000;
  const cc = Math.max(1, chainCount);

  // chain 효과: 각 packet 의 begin 을 (i / cc) * dur 만큼 늦춤 + 전역 phaseOffset 적용
  // SMIL 의 begin 은 음수 가능 → 이미 진행 중인 상태에서 시작.
  const baseOffset = -phaseOffset * durSec;

  return (
    <>
      {Array.from({ length: cc }, (_, i) => {
        const beginSec = baseOffset - (i * durSec) / cc;
        const beginAttr = `${beginSec.toFixed(3)}s`;
        return (
          <Shape
            key={i}
            color={stroke}
            animateMotionProps={{
              dur: `${durSec}s`,
              repeatCount: "indefinite",
              path,
              begin: beginAttr,
            }}
          />
        );
      })}
    </>
  );
}

export const EdgeTraveler = memo(EdgeTravelerImpl);
