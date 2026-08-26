import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

import { Providers } from "@/components/providers";
import { SiteNav } from "@/components/shared/site-nav";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "learn-sys · 个性化教学暨学习资源生产引擎",
  description: "画像输入 → 多智能体协同诊断、教学与审核 → 沉淀可溯源的个性化学习资源",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased`}>
        <Providers>
          <SiteNav />
          <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
