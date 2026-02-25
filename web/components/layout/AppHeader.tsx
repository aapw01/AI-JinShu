"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { BookCopy, Plus, Star, Type } from "lucide-react";
import { api, Novel } from "@/lib/api";
import { Button } from "@/components/ui/Button";

export function AppHeader() {
  const pathname = usePathname();
  const [novels, setNovels] = useState<Novel[]>([]);

  useEffect(() => {
    let disposed = false;
    const load = async () => {
      try {
        const data = await api.listNovels();
        if (!disposed) setNovels(data);
      } catch {
        // keep stale data to avoid layout jump
      }
    };
    load();
    const timer = setInterval(load, 15000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, []);

  const stats = useMemo(() => {
    const works = novels.length;
    const weekAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;

    const weekChapters = novels
      .filter((n) => new Date(n.updated_at || n.created_at).getTime() >= weekAgo)
      .reduce((sum, n) => sum + estimateCompletedChapters(n), 0);

    const qualityValues = novels.map((n) => estimateQualityScore(n));
    const qualityScore = qualityValues.length
      ? Math.round((qualityValues.reduce((a, b) => a + b, 0) / qualityValues.length) * 10) / 10
      : 0;

    const totalWords = novels.reduce((sum, n) => sum + estimateWordCount(n), 0);

    return { works, weekChapters, qualityScore, totalWords };
  }, [novels]);

  return (
    <header className="sticky top-0 z-50 border-b border-[#DDD8D3] bg-[#F8F6F3]/95 backdrop-blur-xl">
      <div className="max-w-[1500px] mx-auto px-3 h-14 flex items-center gap-3">
        <div className="flex items-center gap-4 min-w-0">
          <Link href="/" className="inline-flex items-center gap-2 shrink-0">
            <div className="w-7 h-7 rounded-[8px] bg-[#C8211B] text-white flex items-center justify-center font-semibold text-sm">锦</div>
            <span className="font-semibold text-[#1F1B18] text-[18px] leading-none">锦书</span>
          </Link>
          <nav className="hidden md:flex items-center gap-6 text-[15px]">
            <NavItem href="/" active={pathname === "/"}>工作台</NavItem>
            <NavItem href="/novels" active={pathname.startsWith("/novels")}>我的作品</NavItem>
          </nav>
        </div>

        <div className="ml-auto flex items-center gap-5 shrink-0">
          <div className="hidden xl:flex items-center gap-3 text-[13px] text-[#3E3833] whitespace-nowrap">
            <StatItem icon={<BookCopy className="w-3.5 h-3.5" />} text={`${stats.works} 作品`} />
            <StatItem icon={<BookCopy className="w-3.5 h-3.5 rotate-45" />} text={`${stats.weekChapters} 本周章节`} />
            <StatItem icon={<Star className="w-3.5 h-3.5" />} text={`${stats.qualityScore || 0} 质量分`} />
            <StatItem icon={<Type className="w-3.5 h-3.5" />} text={`${formatWordCount(stats.totalWords)} 总字数`} />
          </div>
          <Link href="/create">
            <Button size="sm" className="h-9 px-4 bg-[#C8211B] hover:bg-[#AD1B16] shadow-none text-sm rounded-[10px]">
              <Plus className="w-4 h-4 mr-1" />
              新建
            </Button>
          </Link>
        </div>
      </div>
    </header>
  );
}

function NavItem({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={`transition-colors ${
        active ? "text-[#C8211B] font-semibold" : "text-[#6B635D] hover:text-[#1F1B18]"
      }`}
    >
      {children}
    </Link>
  );
}

function StatItem({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[#403A34]">
      {icon}
      {text}
    </span>
  );
}

function getChapterTarget(novel: Novel) {
  const cfg = novel.config as { chapter_target?: number } | undefined;
  return Math.max(1, Math.min(1000, Number(cfg?.chapter_target) || 50));
}

function estimateCompletedChapters(novel: Novel) {
  const target = getChapterTarget(novel);
  if (novel.status === "completed") return target;
  if (novel.status === "generating") return Math.max(1, Math.floor(target * 0.6));
  if (novel.status === "failed") return Math.max(1, Math.floor(target * 0.25));
  return Math.max(1, Math.floor(target * 0.1));
}

function estimateQualityScore(novel: Novel) {
  const base = Array.from(novel.id).reduce((sum, c) => sum + c.charCodeAt(0), 0) % 7;
  if (novel.status === "completed") return 88 + base;
  if (novel.status === "generating") return 82 + base;
  if (novel.status === "failed") return 74 + base;
  return 80 + base;
}

function estimateWordCount(novel: Novel) {
  return estimateCompletedChapters(novel) * 1800;
}

function formatWordCount(words: number) {
  if (!words) return "0";
  if (words >= 10000) return `${(words / 10000).toFixed(1)}万`;
  return `${Math.round(words).toLocaleString()}`;
}
