import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "锦书 - AI 小说生成平台",
  description: "取意“云中谁寄锦书来”的 AI 长篇小说创作平台",
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
