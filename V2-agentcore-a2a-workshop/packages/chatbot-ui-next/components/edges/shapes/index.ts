// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { Box } from "./box";
import { Circle } from "./circle";
import type { AnimatedSvg } from "./types";

export const shapes = {
  box: Box,
  circle: Circle,
} satisfies Record<string, AnimatedSvg>;

export type ShapeName = keyof typeof shapes;
export type { AnimatedSvg, AnimatedSvgProps, AnimateMotionProps } from "./types";
