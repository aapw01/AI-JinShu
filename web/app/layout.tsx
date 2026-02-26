import type { Metadata } from "next";
import "./globals.css";
import { AppHeader } from "@/components/layout/AppHeader";

export const metadata: Metadata = {
  title: "锦书 - AI 小说生成平台",
  description: "取意“云中谁寄锦书来”的 AI 长篇小说创作平台",
  icons: {
    icon: "/icon.svg",
    shortcut: "/icon.svg",
    apple: "/icon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh">
      <body className="min-h-screen bg-[#F4F3F1] text-[#1F1B18] antialiased">
        <AppHeader />
        {children}
      </body>
    </html>
  );
}
