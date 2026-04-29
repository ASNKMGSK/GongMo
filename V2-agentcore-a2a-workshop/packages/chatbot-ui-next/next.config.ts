import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 모노레포 루트에도 pnpm-workspace.yaml 이 있어 Next.js 가 inferred root 경고를 출력.
  // chatbot-ui-next 자체를 turbopack root 로 고정해 경고 제거 + import 경로 안정화.
  //
  // ⚠ outputFileTracingRoot 는 turbopack.root 와 *완전히 동일* 해야 함 (Next 16 요구).
  // 불일치 시 dev 서버가 workspace 루트를 module resolve base 로 바꿔
  // `Can't resolve 'tailwindcss'` 같은 에러 발생.
  turbopack: {
    root: path.resolve(__dirname),
  },
  outputFileTracingRoot: path.resolve(__dirname),
  // EC2 standalone 배포 (2026-04-24 복구) — pm2 가 standalone/server.js 실행.
  // Vercel 배포로 전환 시 이 줄을 주석 처리.
  output: "standalone",
};

export default nextConfig;
