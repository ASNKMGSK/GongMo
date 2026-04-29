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
  // 한 변 size = 6 → 박스 전체 width/height ≈ 12px
  // 등각 좌표: x = ±cos(30°)·s ≈ ±5.2,  y = ±sin(30°)·s = ±3
  return (
    <g style={{ filter: "drop-shadow(0 1px 1.5px rgba(0,0,0,0.35))" }}>
      {/* 윗면 (가장 밝음) */}
      <polygon
        points="0,-6 5.2,-3 0,0 -5.2,-3"
        fill="#e3c293"
        stroke="#5a3a18"
        strokeWidth="0.5"
        strokeLinejoin="round"
      />
      {/* 왼쪽 측면 (중간 톤) */}
      <polygon
        points="-5.2,-3 0,0 0,6 -5.2,3"
        fill="#b08756"
        stroke="#5a3a18"
        strokeWidth="0.5"
        strokeLinejoin="round"
      />
      {/* 오른쪽 측면 (가장 어두움) */}
      <polygon
        points="5.2,-3 0,0 0,6 5.2,3"
        fill="#86592f"
        stroke="#5a3a18"
        strokeWidth="0.5"
        strokeLinejoin="round"
      />
      {/* 윗면 테이프 라인 */}
      <line x1="0" y1="-6" x2="0" y2="0" stroke="#5a3a18" strokeWidth="0.4" />
      <animateMotion {...animateMotionProps} />
    </g>
  );
};
