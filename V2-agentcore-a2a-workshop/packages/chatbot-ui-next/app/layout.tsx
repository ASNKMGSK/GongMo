// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from "next";
import { Geist_Mono, Sofia_Sans } from "next/font/google";

import { AppShell } from "@/components/AppShell";
import { AppStateProvider } from "@/lib/AppStateContext";
import { ToastProvider } from "@/lib/toast";
import "./globals.css";

// Sofia Sans = Mastercard 공식 fallback. 본문/UI 의 weight 범위 (450/500/700) 담당.
const sofiaSans = Sofia_Sans({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
  weight: ["300", "400", "500", "600", "700"],
});

const geistMono = Geist_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "QA Pipeline V3 Dashboard",
  description:
    "QA Pipeline V3 — 파이프라인 시각화 · 평가 실행 · HITL 검토 · Claude Docs 톤",
};

// 전 페이지에서 useSearchParams / SSE / 동적 데이터 사용 → 정적 prerender 자체를 비활성화.
// Next.js 16 의 missing-suspense-with-csr-bailout 빌드 실패 회피.
export const dynamic = "force-dynamic";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ko"
      className={`${sofiaSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        {/* 초기 flash 방지 — localStorage 테마 선값 주입 */}
        <script
          dangerouslySetInnerHTML={{
            __html: `
              (function(){
                try {
                  var t = localStorage.getItem('qa-theme');
                  if (t === 'dark' || t === 'light') {
                    document.documentElement.setAttribute('data-theme', t);
                  }
                } catch (e) {}
              })();
            `,
          }}
        />
      </head>
      <body className="min-h-full bg-[var(--bg)] text-[var(--ink)] font-sans">
        <ToastProvider>
          {/* AppStateProvider 를 root layout 에 두어 페이지 navigation 사이에도 컨텍스트 유지.
              evaluate/page.tsx 가 unmount/remount 되어도 평가 결과·로그·노드 상태 보존. */}
          <AppStateProvider>
            <AppShell>{children}</AppShell>
          </AppStateProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
