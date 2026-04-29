// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { AnimatedSvg } from "./types";

export const Circle: AnimatedSvg = ({ animateMotionProps, color }) => (
  <circle
    r={3}
    fill={color}
    style={{ filter: `drop-shadow(0 0 3px ${color}aa)` }}
  >
    <animateMotion {...animateMotionProps} />
  </circle>
);
