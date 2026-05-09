// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { AnimatedSvg } from "./types";

/**
 * 등각 투영(isometric) 3D 박스 — ReactFlow UI animated-svg-edge 의 reference 모양.
 * 3개 폴리곤으로 윗면·왼쪽 측면·오른쪽 측면 표현 + 윗면 테이프 라인.
 *
 * 색상은 택배 박스 톤 고정 (color prop 무시) — 엣지 테마가 다양해도 동일한 박스 룩 유지.
 */
export const Box: AnimatedSvg = ({ animateMotionProps }) => {
  // ★ 2026-05-08: 또렷함 우선으로 재조정.
  // - drop-shadow 제거 (외곽 1.5px blur 가 박스 윤곽까지 번지게 만들던 주범)
  // - 한 변 size = 10 → 박스 전체 width/height ≈ 20px (기존 12px 의 1.67배)
  // - stroke 0.5/0.4 → 1.0/0.8 + vector-effect="non-scaling-stroke" 로
  //   ReactFlow viewport zoom 에 stroke 가 같이 줄지 않게 고정.
  // 등각 좌표: x = ±cos(30°)·s ≈ ±8.66,  y = ±sin(30°)·s = ±5
  return (
    <g>
      {/* 윗면 (가장 밝음) */}
      <polygon
        points="0,-10 8.66,-5 0,0 -8.66,-5"
        fill="#e3c293"
        stroke="#5a3a18"
        strokeWidth="1"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {/* 왼쪽 측면 (중간 톤) */}
      <polygon
        points="-8.66,-5 0,0 0,10 -8.66,5"
        fill="#b08756"
        stroke="#5a3a18"
        strokeWidth="1"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {/* 오른쪽 측면 (가장 어두움) */}
      <polygon
        points="8.66,-5 0,0 0,10 8.66,5"
        fill="#86592f"
        stroke="#5a3a18"
        strokeWidth="1"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {/* 윗면 테이프 라인 */}
      <line
        x1="0"
        y1="-10"
        x2="0"
        y2="0"
        stroke="#5a3a18"
        strokeWidth="0.8"
        vectorEffect="non-scaling-stroke"
      />
      <animateMotion {...animateMotionProps} />
    </g>
  );
};
