// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ComponentType } from "react";

/**
 * <animateMotion> 에 그대로 spread 되는 props.
 * path 문자열을 직접 지정 (mpath 대신) — edge 가 자체적으로 path 를 들고 있으므로.
 */
export interface AnimateMotionProps {
  dur: string;
  repeatCount: number | "indefinite";
  path: string;
  rotate?: "auto" | "auto-reverse" | number;
  begin?: string;
}

export interface AnimatedSvgProps {
  animateMotionProps: AnimateMotionProps;
  color: string;
}

export type AnimatedSvg = ComponentType<AnimatedSvgProps>;
