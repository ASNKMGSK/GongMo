// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { Metadata } from "next";
import { Geist, Geist_Mono, Source_Serif_4 } from "next/font/google";

import { AppShell } from "@/components/AppShell";
import { AppStateProvider } from "@/lib/AppStateContext";
import { ToastProvider } from "@/lib/toast";
import "./globals.css";

// Geist Sans = Vercel OSS, Anthropic 의 Söhne / Styrene B 와 가장 가까운 humanist sans
const geistSans = Geist({
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

// Source Serif 4 = Anthropic 의 Tiempos Headline 대체 (display 헤딩용)
const sourceSerif = Source_Serif_4({
  variable: "--font-serif",
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "600", "700"],
  style: ["normal", "italic"],
});

export const metadata: Metadata = {
  title: "QA Pipeline V3 Dashboard",
  description:
    "QA Pipeline V3 — 파이프라인 시각화 · 평가 실행 · HITL 검토 · Claude Docs 톤",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ko"
      className={`${geistSans.variable} ${geistMono.variable} ${sourceSerif.variable} h-full antialiased`}
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
