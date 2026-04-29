// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { memo } from "react";

import { statusLightColor } from "@/lib/group";

interface Props {
  pendingCount: number;
  confirmedCount: number;
  forceT3Count: number;
  size?: number;
}

function StatusLight({
  pendingCount,
  confirmedCount,
  forceT3Count,
  size = 13,
}: Props) {
  const { hex, title } = statusLightColor({
    pendingCount,
    confirmedCount,
    forceT3Count,
  });
  return (
    <span
      title={title}
      style={{
        color: hex,
        fontSize: size,
        lineHeight: 1,
        marginRight: 4,
      }}
    >
      &#9679;
    </span>
  );
}

export default memo(StatusLight);
