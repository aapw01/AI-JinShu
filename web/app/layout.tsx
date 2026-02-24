import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI-JinShu - AI 小说生成平台",
  description: "基于 AI 的智能小说创作平台，让创意变为现实",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh">
      <body className="min-h-screen bg-[#F5F5F7] text-[#1D1D1F] antialiased">
        {children}
      </body>
    </html>
  );
}
