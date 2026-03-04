"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowLeft, BarChart3, Clapperboard, Copy, Download, Wand2 } from "lucide-react";
import { api, Novel, NovelVersion, Chapter, ChapterProgress, RewriteAnnotationInput, getErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { TopBar } from "@/components/ui/TopBar";
import { formatNovelStatus } from "@/lib/display";

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

function getDisplayChapterTitle(chapterNum: number, title?: string) {
  const value = (title || "").trim();
  return value || `未命名（第${chapterNum}章）`;
}

function getChapterHeading(chapterNum: number, title?: string) {
  const display = getDisplayChapterTitle(chapterNum, title);
  const compact = display.replace(new RegExp(`^第\\s*${chapterNum}\\s*章[:：\\s-]*`), "").trim();
  return compact ? `第 ${chapterNum} 章 · ${compact}` : `第 ${chapterNum} 章`;
}

export default function NovelPage() {
  const params = useParams();
  const router = useRouter();
  const id = String(params.id);

  const [novel, setNovel] = useState<Novel | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [chapterProgress, setChapterProgress] = useState<ChapterProgress[]>([]);
  const [versions, setVersions] = useState<NovelVersion[]>([]);
  const [activeVersionId, setActiveVersionId] = useState<number | null>(null);
  const [selectedChapterNum, setSelectedChapterNum] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [submittingRewrite, setSubmittingRewrite] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [annotations, setAnnotations] = useState<RewriteAnnotationInput[]>([]);
  const [selectionDraft, setSelectionDraft] = useState<{
    chapter_num: number;
    selected_text: string;
    start_offset: number;
    end_offset: number;
  } | null>(null);
  const [instructionDraft, setInstructionDraft] = useState("");
  const [issueTypeDraft, setIssueTypeDraft] = useState<RewriteAnnotationInput["issue_type"]>("continuity");
  const [priorityDraft, setPriorityDraft] = useState<RewriteAnnotationInput["priority"]>("must");
  const [copyState, setCopyState] = useState<"idle" | "title_copied" | "content_copied" | "error">("idle");
  const contentRef = useRef<HTMLDivElement | null>(null);
  const copyTimerRef = useRef<number | null>(null);

  // Export dropdown
  const [showExport, setShowExport] = useState(false);

  useEffect(() => {
    loadData();
  }, [id]);

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const novelData = await api.getNovel(id);
      let versionsData: NovelVersion[] = [];
      let defaultVersion: NovelVersion | null = null;
      try {
        versionsData = await api.getVersions(id);
        defaultVersion = versionsData.find((v) => v.is_default) || versionsData[0] || null;
      } catch (versionErr) {
        // Fallback for legacy/inconsistent environments where versions endpoint may fail.
        console.error(versionErr);
      }
      const [progressData, chaptersData] = await Promise.all([
        api.getChapterProgress(id),
        api.getChapters(id, defaultVersion?.id),
      ]);
      setNovel(novelData);
      setChapters(chaptersData);
      setChapterProgress(progressData);
      setVersions(versionsData);
      setActiveVersionId(defaultVersion?.id || null);
      if (progressData.length > 0 && selectedChapterNum === null) {
        setSelectedChapterNum(progressData[0].chapter_num);
      }
    } catch (err) {
      setError(getErrorMessage(err, "加载失败"));
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
  const novelTitle = novel?.title || "";
  const selectedChapterTitleDisplay = selectedChapter
    ? getDisplayChapterTitle(selectedChapter.chapter_num, selectedChapter.title)
    : "";
  const topBarTitle = selectedChapter
    ? getChapterHeading(selectedChapter.chapter_num, selectedChapter.title)
    : novelTitle;
  const topBarSubtitle = selectedChapter
    ? [novelTitle, novel?.genre, novel?.style].filter(Boolean).join(" · ")
    : [novel?.genre, novel?.style].filter(Boolean).join(" · ");

  const resetCopyState = () => {
    if (copyTimerRef.current) {
      window.clearTimeout(copyTimerRef.current);
      copyTimerRef.current = null;
    }
    copyTimerRef.current = window.setTimeout(() => {
      setCopyState("idle");
      copyTimerRef.current = null;
    }, 1400);
  };

  const handleCopyTitle = async () => {
    if (!selectedChapter) return;
    try {
      await navigator.clipboard.writeText(selectedChapterTitleDisplay);
      setCopyState("title_copied");
      resetCopyState();
    } catch (e) {
      console.error(e);
      setCopyState("error");
      resetCopyState();
    }
  };

  const handleCopyContent = async () => {
    const text = (selectedChapter?.content || "").trim();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopyState("content_copied");
      resetCopyState();
    } catch (e) {
      console.error(e);
      setCopyState("error");
      resetCopyState();
    }
  };

  useEffect(() => () => {
    if (copyTimerRef.current) {
      window.clearTimeout(copyTimerRef.current);
    }
  }, []);

  const onContentMouseUp = () => {
    if (!selectedChapter || !contentRef.current) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    if (!contentRef.current.contains(range.commonAncestorContainer)) return;
    const selectedText = sel.toString().trim();
    if (!selectedText) return;

    const preRange = range.cloneRange();
    preRange.selectNodeContents(contentRef.current);
    preRange.setEnd(range.startContainer, range.startOffset);
    const startOffset = preRange.toString().length;
    const endOffset = startOffset + selectedText.length;
    setSelectionDraft({
      chapter_num: selectedChapter.chapter_num,
      selected_text: selectedText,
      start_offset: startOffset,
      end_offset: endOffset,
    });
  };

  const addAnnotation = () => {
    if (!selectionDraft || !instructionDraft.trim()) return;
    setAnnotations((prev) => [
      ...prev,
      {
        ...selectionDraft,
        instruction: instructionDraft.trim(),
        issue_type: issueTypeDraft,
        priority: priorityDraft,
      },
    ]);
    setSelectionDraft(null);
    setInstructionDraft("");
  };

  const removeAnnotation = (index: number) => {
    setAnnotations((prev) => prev.filter((_, i) => i !== index));
  };

  const submitRewrite = async () => {
    if (!activeVersionId || annotations.length === 0) return;
    try {
      setSubmittingRewrite(true);
      const res = await api.createRewriteRequest(id, {
        base_version_id: activeVersionId,
        annotations,
      });
      setAnnotations([]);
      router.push(`/novels/${id}/progress?rewrite_request_id=${res.id}`);
    } catch (e) {
      console.error(e);
      setError("提交重写建议失败");
    } finally {
      setSubmittingRewrite(false);
    }
  };

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
        title={topBarTitle}
        subtitle={topBarSubtitle}
        backHref="/novels"
        icon={<ArrowLeft className="w-5 h-5" />}
        actions={(
            <div className="flex items-center gap-2">
            <div className="w-[180px]">
              <Select
                value={String(activeVersionId ?? "")}
                onValueChange={async (nextValue) => {
                  const nextId = Number(nextValue);
                  setActiveVersionId(nextId);
                  const nextChapters = await api.getChapters(id, nextId);
                  setChapters(nextChapters);
                }}
                className="h-9 px-3 py-2 text-sm bg-[#FFFDFB]"
                options={versions.map((v) => ({
                  value: String(v.id),
                  label: `版本 v${v.version_no}${v.is_default ? "（默认）" : ""}`,
                }))}
              />
            </div>
            <Button
              size="sm"
              className="h-9 px-3"
              disabled={!annotations.length || !activeVersionId}
              loading={submittingRewrite}
              onClick={submitRewrite}
            >
              <Wand2 className="w-4 h-4 mr-1.5" />
              提交修改建议
            </Button>
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
              {STATUS_MAP[novel.status]?.label || formatNovelStatus(novel.status)}
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
            {novel.status === "completed" ? (
              <Link href={`/storyboards/create?novel_id=${encodeURIComponent(id)}`}>
                <Button
                  variant="secondary"
                  size="sm"
                  className="h-9 px-3 shadow-none border-[#E5DED7] bg-white hover:bg-[#F8F5F1]"
                >
                  <Clapperboard className="w-4 h-4 mr-1.5" />
                  生成导演分镜脚本
                </Button>
              </Link>
            ) : null}
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
            className="grid grid-cols-1 lg:grid-cols-5 gap-6"
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
                      <div className="text-xs opacity-70 truncate">
                        {getDisplayChapterTitle(chapter.chapter_num, chapter.title)}
                      </div>
                    </button>
                  ))}
                </div>
              </Card>
            </aside>

            <div className="lg:col-span-3">
              <Card className="p-8">
                {selectedChapter ? (
                  <article className="max-w-none">
                    <div className="mb-5 flex items-center justify-between gap-3">
                      <p className="text-sm text-[#7E756D]">
                        {selectedChapter.word_count ? `${selectedChapter.word_count.toLocaleString()} 字` : "字数待统计"}
                      </p>
                      <div className="flex items-center gap-2">
                        <Button
                          variant="secondary"
                          size="sm"
                          className="h-8 px-3 shadow-none border-[#E5DED7] bg-white hover:bg-[#F8F5F1]"
                          onClick={handleCopyTitle}
                        >
                          <Copy className="w-3.5 h-3.5 mr-1.5" />
                          {copyState === "title_copied" ? "已复制标题" : "复制标题"}
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          className="h-8 px-3 shadow-none border-[#E5DED7] bg-white hover:bg-[#F8F5F1]"
                          onClick={handleCopyContent}
                          disabled={!selectedChapter.content?.trim()}
                        >
                          <Copy className="w-3.5 h-3.5 mr-1.5" />
                          {copyState === "content_copied" ? "已复制正文" : "复制正文"}
                        </Button>
                      </div>
                    </div>
                    {copyState === "error" ? (
                      <p className="mb-4 text-xs text-[#C4372D]">复制失败，请手动复制。</p>
                    ) : null}
                    <div
                      ref={contentRef}
                      onMouseUp={onContentMouseUp}
                      className="text-[#3A3A3C] leading-relaxed whitespace-pre-wrap cursor-text"
                    >
                      {selectedChapter.content || "暂无内容"}
                    </div>
                    {(typeof selectedChapter.language_quality_score === "number" || selectedChapter.language_quality_report) && (
                      <div className="mt-6 border-t border-[rgba(60,60,67,0.12)] pt-4 text-xs text-[#7E756D]">
                        语言质量：{typeof selectedChapter.language_quality_score === "number"
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

            <aside className="lg:col-span-1">
              <Card className="p-4 sticky top-24">
                <h3 className="text-sm font-medium text-[#7E756D] mb-3">修改建议</h3>
                <p className="text-xs text-[#8E8379] mb-3">
                  选中文本后添加建议，系统会从最早命中章节开始级联重写。
                </p>
                {selectionDraft ? (
                  <div className="space-y-2 border border-[#E5DED7] rounded-lg p-3 bg-[#FFFDFB] mb-3">
                    <p className="text-xs text-[#8E8379]">第 {selectionDraft.chapter_num} 章</p>
                    <p className="text-xs text-[#5E5650] line-clamp-3">“{selectionDraft.selected_text}”</p>
                    <div className="grid grid-cols-2 gap-2">
                      <Select
                        value={issueTypeDraft}
                        onValueChange={(v) => setIssueTypeDraft(v as RewriteAnnotationInput["issue_type"])}
                        className="h-8 rounded-md px-2 py-1 text-xs"
                        options={[
                          { value: "continuity", label: "连续性" },
                          { value: "bug", label: "逻辑问题" },
                          { value: "style", label: "文风" },
                          { value: "pace", label: "节奏" },
                          { value: "other", label: "其他" },
                        ]}
                      />
                      <Select
                        value={priorityDraft}
                        onValueChange={(v) => setPriorityDraft(v as RewriteAnnotationInput["priority"])}
                        className="h-8 rounded-md px-2 py-1 text-xs"
                        options={[
                          { value: "must", label: "必须" },
                          { value: "should", label: "建议" },
                          { value: "nice", label: "可选" },
                        ]}
                      />
                    </div>
                    <textarea
                      value={instructionDraft}
                      onChange={(e) => setInstructionDraft(e.target.value)}
                      placeholder="输入修改方向，例如：角色伤势应延续到下一章，避免双手持剑。"
                      className="w-full min-h-[90px] rounded-md border border-[#E5DED7] bg-white p-2 text-xs outline-none"
                    />
                    <Button size="sm" className="w-full" onClick={addAnnotation}>
                      添加到建议列表
                    </Button>
                  </div>
                ) : (
                  <p className="text-xs text-[#8E8379] mb-3">在正文中选中一段文本后，这里会出现标注编辑器。</p>
                )}

                <div className="space-y-2 max-h-[45vh] overflow-y-auto">
                  {annotations.map((ann, idx) => (
                    <div key={`${ann.chapter_num}-${idx}`} className="border border-[#E5DED7] rounded-lg p-2 bg-white">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-xs font-medium text-[#5E5650]">第 {ann.chapter_num} 章 · {ann.priority}</p>
                        <button
                          className="text-xs text-[#C4372D]"
                          onClick={() => removeAnnotation(idx)}
                        >
                          删除
                        </button>
                      </div>
                      {ann.selected_text ? (
                        <p className="text-xs text-[#7E756D] line-clamp-2 mt-1">“{ann.selected_text}”</p>
                      ) : null}
                      <p className="text-xs text-[#3A3A3C] mt-1">{ann.instruction}</p>
                    </div>
                  ))}
                  {!annotations.length ? (
                    <p className="text-xs text-[#8E8379]">暂无建议</p>
                  ) : null}
                </div>
              </Card>
            </aside>
          </motion.div>
        )}
      </div>
    </main>
  );
}
