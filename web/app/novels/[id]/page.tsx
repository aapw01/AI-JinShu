"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowLeft, BarChart3, Download } from "lucide-react";
import { api, Novel, Chapter, ChapterProgress } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TopBar } from "@/components/ui/TopBar";

const STATUS_MAP: Record<string, { label: string; variant: "default" | "success" | "warning" | "error" }> = {
  draft: { label: "草稿", variant: "default" },
  generating: { label: "生成中", variant: "warning" },
  completed: { label: "已完成", variant: "success" },
  failed: { label: "失败", variant: "error" },
};

const CHAPTER_STATUS_MAP: Record<ChapterProgress["status"], { label: string; variant: "default" | "success" | "warning" | "error" }> = {
  pending: { label: "待生成", variant: "default" },
  generating: { label: "生成中", variant: "warning" },
  completed: { label: "已完成", variant: "success" },
};

export default function NovelPage() {
  const params = useParams();
  const router = useRouter();
  const id = String(params.id);

  const [novel, setNovel] = useState<Novel | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [chapterProgress, setChapterProgress] = useState<ChapterProgress[]>([]);
  const [selectedChapterNum, setSelectedChapterNum] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Export dropdown
  const [showExport, setShowExport] = useState(false);

  useEffect(() => {
    loadData();
  }, [id]);

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const [novelData, chaptersData, progressData] = await Promise.all([
        api.getNovel(id),
        api.getChapters(id),
        api.getChapterProgress(id),
      ]);
      setNovel(novelData);
      setChapters(chaptersData);
      setChapterProgress(progressData);
      if (progressData.length > 0 && selectedChapterNum === null) {
        setSelectedChapterNum(progressData[0].chapter_num);
      }
    } catch (err) {
      setError("加载失败");
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const selectedChapter = selectedChapterNum !== null
    ? chapters.find((c) => c.chapter_num === selectedChapterNum) || null
    : null;
  const selectedChapterMeta = selectedChapterNum !== null
    ? chapterProgress.find((c) => c.chapter_num === selectedChapterNum) || null
    : null;

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin w-8 h-8 border-2 border-[#C8211B] border-t-transparent rounded-full" />
      </div>
    );
  }

  if (error || !novel) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4">
        <p className="text-[#C4372D]">{error || "小说不存在"}</p>
        <Button variant="secondary" onClick={() => router.push("/novels")}>
          返回列表
        </Button>
      </div>
    );
  }

  return (
    <main className="min-h-screen">
      <TopBar
        title={novel.title}
        subtitle={[novel.genre, novel.style].filter(Boolean).join(" · ")}
        backHref="/novels"
        icon={<ArrowLeft className="w-5 h-5" />}
        actions={(
          <div className="flex items-center gap-2">
            <div className="inline-flex h-9 items-center gap-1.5 rounded-full border border-[#E5DED7] bg-[#FFFDFB] px-3 text-sm font-medium text-[#6F665F]">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  novel.status === "completed"
                    ? "bg-[#18864B]"
                    : novel.status === "failed"
                    ? "bg-[#C4372D]"
                    : novel.status === "generating"
                    ? "bg-[#D08A10]"
                    : "bg-[#8E8379]"
                }`}
              />
              {STATUS_MAP[novel.status]?.label || novel.status}
            </div>
            <div className="relative">
              <Button
                variant="secondary"
                size="sm"
                className="h-9 px-3 shadow-none border-[#E5DED7] bg-white hover:bg-[#F8F5F1]"
                onClick={() => setShowExport(!showExport)}
              >
                <Download className="w-4 h-4 mr-2" />
                导出
              </Button>
              {showExport && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setShowExport(false)} />
                  <div className="absolute right-0 mt-1.5 w-40 bg-white border border-[#E5DED7] rounded-[12px] shadow-[0_10px_30px_rgba(0,0,0,0.08)] z-20 overflow-hidden">
                    {(["txt", "md", "zip"] as const).map((format) => (
                      <a
                        key={format}
                        href={api.getExportUrl(id, format)}
                        download
                        className="block px-4 py-2.5 text-sm text-[#3A3A3C] hover:bg-[#F6F3EF] transition-colors"
                        onClick={() => setShowExport(false)}
                      >
                        导出为 .{format}
                      </a>
                    ))}
                  </div>
                </>
              )}
            </div>
            <Link href={`/novels/${id}/progress`}>
              <Button
                variant="secondary"
                size="sm"
                className="h-9 px-3 shadow-none border-[#E5DED7] bg-white hover:bg-[#F8F5F1]"
              >
                <BarChart3 className="w-4 h-4 mr-1.5" />
                进度
              </Button>
            </Link>
          </div>
        )}
      />

      <div className="max-w-6xl mx-auto px-4 py-6">
        {chapterProgress.length === 0 ? (
          <EmptyState
            icon={(
              <svg className="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            )}
            title="章节目录准备中"
            description="请稍后刷新，或前往进度页查看任务状态。"
            action={(
              <Link href={`/novels/${id}/progress`}>
                <Button>查看进度</Button>
              </Link>
            )}
          />
        ) : (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, ease: [0.25, 0.1, 0.25, 1] }}
            className="grid grid-cols-1 lg:grid-cols-4 gap-6"
          >
            <aside className="lg:col-span-1">
              <Card className="p-4 sticky top-24">
                <h3 className="text-sm font-medium text-[#7E756D] mb-4">章节目录</h3>
                <div className="space-y-1 max-h-[60vh] overflow-y-auto">
                  {chapterProgress.map((chapter) => (
                    <button
                      key={chapter.chapter_num}
                      onClick={() => setSelectedChapterNum(chapter.chapter_num)}
                      className={`
                        w-full text-left px-3 py-2 rounded-lg text-sm transition-all
                        ${selectedChapterNum === chapter.chapter_num
                          ? "bg-[#F8ECEA] text-[#A52A25] border border-[#EED1CC]"
                          : "text-[#7E756D] hover:bg-[#F6F3EF] hover:text-[#1F1B18]"
                        }
                      `}
                    >
                      <div className="font-medium flex items-center justify-between gap-2">
                        <span>第 {chapter.chapter_num} 章</span>
                        <Badge variant={CHAPTER_STATUS_MAP[chapter.status].variant}>
                          {CHAPTER_STATUS_MAP[chapter.status].label}
                        </Badge>
                      </div>
                      {chapter.title && (
                        <div className="text-xs opacity-70 truncate">{chapter.title}</div>
                      )}
                    </button>
                  ))}
                </div>
              </Card>
            </aside>

            <div className="lg:col-span-3">
              <Card className="p-8">
                {selectedChapter ? (
                  <article className="max-w-none">
                    <h2 className="text-xl font-semibold text-[#1F1B18] mb-2">
                      第 {selectedChapter.chapter_num} 章
                      {selectedChapter.title && ` · ${selectedChapter.title}`}
                    </h2>
                    {selectedChapter.word_count && (
                      <p className="text-sm text-[#7E756D] mb-6">
                        {selectedChapter.word_count.toLocaleString()} 字
                      </p>
                    )}
                    <div className="text-[#3A3A3C] leading-relaxed whitespace-pre-wrap">
                      {selectedChapter.content || "暂无内容"}
                    </div>
                    {(selectedChapter.language_quality_score !== undefined || selectedChapter.language_quality_report) && (
                      <div className="mt-6 border-t border-[rgba(60,60,67,0.12)] pt-4 text-xs text-[#7E756D]">
                        语言质量：{selectedChapter.language_quality_score !== undefined
                          ? (selectedChapter.language_quality_score * 10).toFixed(1)
                          : "-"} / 10
                        {selectedChapter.language_quality_report && (
                          <p className="mt-2 text-[#7E756D] whitespace-pre-wrap">{selectedChapter.language_quality_report}</p>
                        )}
                      </div>
                    )}
                  </article>
                ) : (
                  <div className="text-center py-12 text-[#7E756D]">
                    {selectedChapterMeta?.status === "pending" && "该章节待生成"}
                    {selectedChapterMeta?.status === "generating" && "该章节生成中"}
                    {!selectedChapterMeta && "选择一个章节开始阅读"}
                  </div>
                )}
              </Card>
            </div>
          </motion.div>
        )}
      </div>
    </main>
  );
}
