"use client";

import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowLeft, BarChart3, ChevronDown, ChevronRight, CircleAlert, Copy, Download, Wand2, X } from "lucide-react";
import {
  api,
  Chapter,
  ChapterProgress,
  NovelVersion,
  RewriteAnnotationInput,
  getErrorMessage,
} from "@/lib/api";
import { parseChapterContent } from "@/lib/novelContent";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { ConfirmModal } from "@/components/ui";
import { EmptyState } from "@/components/ui/EmptyState";
import { Select } from "@/components/ui/Select";
import { TopBar } from "@/components/ui/TopBar";
import {
  formatNovelStatus,
  getNovelStatusVariant,
  resolveNovelDisplayStatus,
  shouldOpenNovelProgress,
} from "@/lib/display";
import { useNovelPageBaseData, useNovelVersionData } from "@/hooks/use-novel-page-data";

const STATUS_MAP: Record<string, { label: string; variant: "default" | "success" | "warning" | "error" | "info" }> = {
  draft: { label: "草稿", variant: "default" },
  queued: { label: "排队中", variant: "warning" },
  dispatching: { label: "调度中", variant: "warning" },
  generating: { label: "生成中", variant: "warning" },
  awaiting_outline_confirmation: { label: "待确认大纲", variant: "info" },
  paused: { label: "已暂停", variant: "info" },
  completed: { label: "已完成", variant: "success" },
  failed: { label: "失败", variant: "error" },
  cancelled: { label: "已取消", variant: "info" },
};

const CHAPTER_STATUS_MAP: Record<ChapterProgress["status"], { label: string; variant: "default" | "success" | "warning" | "error" }> = {
  pending: { label: "待生成", variant: "default" },
  generating: { label: "生成中", variant: "warning" },
  completed: { label: "已完成", variant: "success" },
  blocked: { label: "已阻断", variant: "error" },
};
const GRAMMAR_SKIPPED_TEXT = "未启用语法检查（language_tool_python 不可用），跳过该项。";
const EMPTY_VERSIONS: NovelVersion[] = [];
const EMPTY_CHAPTERS: Chapter[] = [];
const EMPTY_CHAPTER_PROGRESS: ChapterProgress[] = [];

function getDisplayChapterTitle(chapterNum: number, title?: string) {
  const value = (title || "").trim();
  return value || `未命名（第${chapterNum}章）`;
}

function getChapterHeading(chapterNum: number, title?: string) {
  const display = getDisplayChapterTitle(chapterNum, title);
  const compact = display.replace(new RegExp(`^第\\s*${chapterNum}\\s*章[:：\\s-]*`), "").trim();
  return compact ? `第 ${chapterNum} 章 · ${compact}` : `第 ${chapterNum} 章`;
}

function countVisibleChars(content?: string): number {
  const text = (content || "").trim();
  if (!text) return 0;
  return text.replace(/\s+/g, "").length;
}

function toDisplayLabel(rawValue: string | undefined, labelMap: Record<string, string>, fallback: string): string {
  if (!rawValue) return "";
  return labelMap[rawValue] || fallback;
}

function sanitizeLanguageReport(report?: string): string {
  if (!report) return "";
  return report.replaceAll(GRAMMAR_SKIPPED_TEXT, "").replace(/\s{2,}/g, " ").trim();
}

export default function NovelPage() {
  const params = useParams();
  const router = useRouter();
  const id = String(params.id);

  const [activeVersionId, setActiveVersionId] = useState<number | null>(null);
  const [selectedChapterNum, setSelectedChapterNum] = useState<number | null>(null);
  const [expandedVolumeNos, setExpandedVolumeNos] = useState<Set<number>>(new Set());
  const [submittingRewrite, setSubmittingRewrite] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showIncrementalOutlineHint, setShowIncrementalOutlineHint] = useState(true);
  const [pendingVersionId, setPendingVersionId] = useState<number | null>(null);

  const [annotations, setAnnotations] = useState<RewriteAnnotationInput[]>([]);
  const [selectionDraft, setSelectionDraft] = useState<{
    chapter_num: number;
    selected_text: string;
    start_offset: number;
    end_offset: number;
  } | null>(null);
  const [selectionBubble, setSelectionBubble] = useState<{ left: number; top: number } | null>(null);
  const [instructionDraft, setInstructionDraft] = useState("");
  const [issueTypeDraft, setIssueTypeDraft] = useState<RewriteAnnotationInput["issue_type"]>("continuity");
  const [priorityDraft, setPriorityDraft] = useState<RewriteAnnotationInput["priority"]>("must");

  const [copyState, setCopyState] = useState<"idle" | "title_copied" | "content_copied" | "error">("idle");
  const contentRef = useRef<HTMLDivElement | null>(null);
  const selectionBubbleRef = useRef<HTMLDivElement | null>(null);
  const copyTimerRef = useRef<number | null>(null);
  const hadIncrementalOutlineHintRef = useRef(false);

  const [showExport, setShowExport] = useState(false);
  const {
    data: baseData,
    isLoading: baseLoading,
    error: baseQueryError,
  } = useNovelPageBaseData(id);
  const novel = baseData?.novel ?? null;
  const versions = baseData?.versions ?? EMPTY_VERSIONS;
  const activeGenerationTask = baseData?.activeGenerationTask ?? null;
  const genreLabelMap = baseData?.genreLabelMap ?? {};
  const styleLabelMap = baseData?.styleLabelMap ?? {};
  const defaultVersionId = baseData?.defaultVersion?.id ?? null;

  useEffect(() => {
    if (!defaultVersionId) {
      setActiveVersionId((prev) => (prev === null ? prev : null));
      return;
    }
    setActiveVersionId((prev) => {
      if (prev && versions.some((item) => item.id === prev)) return prev;
      return defaultVersionId;
    });
  }, [defaultVersionId, versions]);

  const {
    data: versionData,
    isLoading: versionLoading,
    error: versionQueryError,
  } = useNovelVersionData(id, activeVersionId);
  const chapters = versionData?.chapters ?? EMPTY_CHAPTERS;
  const chapterProgress = versionData?.chapterProgress ?? EMPTY_CHAPTER_PROGRESS;
  const loading = baseLoading || (activeVersionId !== null && versionLoading);
  const queryErrorMessage = baseQueryError
    ? getErrorMessage(baseQueryError, "小说详情加载失败")
    : versionQueryError
    ? getErrorMessage(versionQueryError, "章节内容加载失败")
    : null;
  const pageError = error || queryErrorMessage;

  const selectedChapter = selectedChapterNum !== null
    ? chapters.find((c) => c.chapter_num === selectedChapterNum) || null
    : null;
  const selectedChapterMeta = selectedChapterNum !== null
    ? chapterProgress.find((c) => c.chapter_num === selectedChapterNum) || null
    : null;
  const groupedVolumes = useMemo(() => {
    const groups = new Map<number, {
      volumeNo: number;
      startChapter: number;
      endChapter: number;
      total: number;
      completed: number;
      chapters: ChapterProgress[];
    }>();
    const sortedChapters = [...chapterProgress].sort((a, b) => a.chapter_num - b.chapter_num);
    for (const chapter of sortedChapters) {
      const volumeNo = Number.isFinite(chapter.volume_no) ? chapter.volume_no : 1;
      const existing = groups.get(volumeNo);
      if (!existing) {
        groups.set(volumeNo, {
          volumeNo,
          startChapter: chapter.chapter_num,
          endChapter: chapter.chapter_num,
          total: 1,
          completed: chapter.status === "completed" ? 1 : 0,
          chapters: [chapter],
        });
        continue;
      }
      existing.startChapter = Math.min(existing.startChapter, chapter.chapter_num);
      existing.endChapter = Math.max(existing.endChapter, chapter.chapter_num);
      existing.total += 1;
      if (chapter.status === "completed") {
        existing.completed += 1;
      }
      existing.chapters.push(chapter);
    }

    return Array.from(groups.values())
      .sort((a, b) => a.volumeNo - b.volumeNo)
      .map((group) => ({
        ...group,
        chapters: [...group.chapters].sort((a, b) => a.chapter_num - b.chapter_num),
      }));
  }, [chapterProgress]);

  const incrementalOutlineHint = useMemo(() => {
    if (!activeGenerationTask) return null;
    if (!shouldOpenNovelProgress(activeGenerationTask.status)) return null;
    const targetChapters = Number(activeGenerationTask.total_chapters || 0);
    if (!Number.isFinite(targetChapters) || targetChapters <= 0) return null;
    const outlinedChapters = chapterProgress.reduce((max, chapter) => Math.max(max, chapter.chapter_num), 0);
    if (outlinedChapters <= 0 || outlinedChapters >= targetChapters) return null;
    return {
      outlinedChapters,
      targetChapters,
    };
  }, [activeGenerationTask, chapterProgress]);

  useEffect(() => {
    const hasHint = Boolean(incrementalOutlineHint);
    if (!hasHint) {
      setShowIncrementalOutlineHint(true);
      hadIncrementalOutlineHintRef.current = false;
      return;
    }
    if (!hadIncrementalOutlineHintRef.current) {
      setShowIncrementalOutlineHint(true);
    }
    hadIncrementalOutlineHintRef.current = true;
  }, [incrementalOutlineHint]);

  const novelTitle = novel?.title || "未命名小说";
  const displayNovelStatus = resolveNovelDisplayStatus(novel?.status, activeGenerationTask);
  const selectedChapterTitleDisplay = selectedChapter
    ? getDisplayChapterTitle(selectedChapter.chapter_num, selectedChapter.title)
    : "";

  const selectedWordCount = useMemo(() => {
    if (!selectedChapter) return 0;
    if (typeof selectedChapter.word_count === "number" && selectedChapter.word_count >= 0) {
      return selectedChapter.word_count;
    }
    return countVisibleChars(selectedChapter.content);
  }, [selectedChapter]);
  const selectedLanguageReport = useMemo(
    () => sanitizeLanguageReport(selectedChapter?.language_quality_report),
    [selectedChapter?.language_quality_report]
  );

  const genreLabel = toDisplayLabel(novel?.genre, genreLabelMap, "未定义体裁");
  const styleLabel = toDisplayLabel(novel?.style, styleLabelMap, "未定义风格");
  const chapterLabel = selectedChapter
    ? getChapterHeading(selectedChapter.chapter_num, selectedChapter.title)
    : "";

  const topBarTitle = novelTitle;
  const topBarSubtitle = [chapterLabel, genreLabel, styleLabel].filter(Boolean).join(" · ");
  const annotationCountByChapter = useMemo(() => {
    const out = new Map<number, number>();
    for (const ann of annotations) {
      out.set(ann.chapter_num, (out.get(ann.chapter_num) || 0) + 1);
    }
    return out;
  }, [annotations]);
  const selectedChapterAnnotations = useMemo(() => {
    if (!selectedChapter) return [];
    return annotations
      .map((ann, index) => ({ ...ann, _index: index }))
      .filter((ann) => ann.chapter_num === selectedChapter.chapter_num);
  }, [annotations, selectedChapter]);

  const selectedChapterAnnotationRanges = useMemo(() => {
    if (!selectedChapter?.content) return [];
    const total = selectedChapter.content.length;
    return selectedChapterAnnotations
      .map((ann) => {
        if (typeof ann.start_offset !== "number" || typeof ann.end_offset !== "number") {
          return null;
        }
        const start = Math.max(0, Math.min(total, ann.start_offset));
        const end = Math.max(0, Math.min(total, ann.end_offset));
        if (end <= start) return null;
        return { ...ann, start, end };
      })
      .filter((ann): ann is RewriteAnnotationInput & { _index: number; start: number; end: number } => ann !== null)
      .sort((a, b) => a.start - b.start);
  }, [selectedChapter?.content, selectedChapterAnnotations]);

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

  const clearSelectionDraft = () => {
    setSelectionDraft(null);
    setSelectionBubble(null);
    setInstructionDraft("");
    const selection = window.getSelection();
    if (selection) selection.removeAllRanges();
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

  useEffect(() => {
    setSelectionDraft(null);
    setSelectionBubble(null);
    setInstructionDraft("");
  }, [selectedChapterNum, activeVersionId]);

  useEffect(() => {
    if (chapterProgress.length === 0) {
      setSelectedChapterNum(null);
      return;
    }
    if (selectedChapterNum === null || !chapterProgress.some((item) => item.chapter_num === selectedChapterNum)) {
      setSelectedChapterNum(chapterProgress[0].chapter_num);
    }
  }, [chapterProgress, selectedChapterNum]);

  useEffect(() => {
    if (groupedVolumes.length === 0) {
      setExpandedVolumeNos(new Set());
      return;
    }
    const selectedVolume = selectedChapterNum === null
      ? null
      : groupedVolumes.find((group) => group.chapters.some((chapter) => chapter.chapter_num === selectedChapterNum));
    const defaultVolume = selectedVolume || groupedVolumes[0];
    setExpandedVolumeNos(new Set([defaultVolume.volumeNo]));
  }, [activeVersionId, groupedVolumes]);

  useEffect(() => {
    if (selectedChapterNum === null || groupedVolumes.length === 0) return;
    const selectedVolume = groupedVolumes.find((group) =>
      group.chapters.some((chapter) => chapter.chapter_num === selectedChapterNum)
    );
    if (!selectedVolume) return;
    setExpandedVolumeNos((prev) => {
      if (prev.has(selectedVolume.volumeNo)) {
        return prev;
      }
      const next = new Set(prev);
      next.add(selectedVolume.volumeNo);
      return next;
    });
  }, [selectedChapterNum, groupedVolumes]);

  useEffect(() => {
    if (!selectionDraft) return;

    const onMouseDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (selectionBubbleRef.current?.contains(target)) return;
      clearSelectionDraft();
    };

    const onEsc = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        clearSelectionDraft();
      }
    };

    window.addEventListener("mousedown", onMouseDown);
    window.addEventListener("keydown", onEsc);
    return () => {
      window.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("keydown", onEsc);
    };
  }, [selectionDraft]);

  const onContentMouseUp = () => {
    if (!selectedChapter || !contentRef.current) return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);
    if (!contentRef.current.contains(range.commonAncestorContainer)) return;

    const selectedText = sel.toString().trim();
    if (!selectedText) {
      clearSelectionDraft();
      return;
    }

    const preRange = range.cloneRange();
    preRange.selectNodeContents(contentRef.current);
    preRange.setEnd(range.startContainer, range.startOffset);
    const startOffset = preRange.toString().length;
    const endOffset = startOffset + selectedText.length;

    const rect = range.getBoundingClientRect();
    const bubbleWidth = 360;
    const bubbleHeight = 280;
    const edge = 12;

    let left = rect.left + rect.width / 2 - bubbleWidth / 2;
    left = Math.max(edge, Math.min(left, window.innerWidth - bubbleWidth - edge));

    let top = rect.bottom + 10;
    if (top + bubbleHeight > window.innerHeight - edge) {
      top = Math.max(edge, rect.top - bubbleHeight - 10);
    }

    setSelectionDraft({
      chapter_num: selectedChapter.chapter_num,
      selected_text: selectedText,
      start_offset: startOffset,
      end_offset: endOffset,
    });
    setSelectionBubble({ left, top });
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
    clearSelectionDraft();
  };

  const removeAnnotation = (index: number) => {
    setAnnotations((prev) => prev.filter((_, i) => i !== index));
  };

  const toggleVolumeExpand = (volumeNo: number) => {
    setExpandedVolumeNos((prev) => {
      const next = new Set(prev);
      if (next.has(volumeNo)) {
        next.delete(volumeNo);
      } else {
        next.add(volumeNo);
      }
      return next;
    });
  };

  const renderSelectedChapterContent = (): ReactNode => {
    const content = selectedChapter?.content || "";
    if (!content.trim()) {
      return "暂无内容";
    }

    if (selectedChapterAnnotationRanges.length) {
      const nodes: ReactNode[] = [];
      let cursor = 0;
      let chunkId = 0;
      for (const ann of selectedChapterAnnotationRanges) {
        if (ann.start < cursor) continue;
        if (ann.start > cursor) nodes.push(content.slice(cursor, ann.start));
        const snippet = content.slice(ann.start, ann.end);
        nodes.push(
          <span
            key={`ann-${ann._index}-${chunkId++}`}
            className="rounded-[6px] bg-[#FFE8E5] px-1 py-0.5 text-[#A52A25] border border-[#FFD2CC] inline cursor-pointer"
            onClick={(event) => { event.stopPropagation(); removeAnnotation(ann._index); }}
            onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); removeAnnotation(ann._index); } }}
            role="button"
            tabIndex={0}
            title="点击删除该建议"
          >
            {snippet}
          </span>
        );
        cursor = ann.end;
      }
      if (cursor < content.length) nodes.push(content.slice(cursor));
      return <div className="whitespace-pre-wrap">{nodes}</div>;
    }

    const displayParas = parseChapterContent(content);
    if (!displayParas.length) return "暂无内容";
    return (
      <div className="novel-content">
        {displayParas.map((para, i) =>
          para.type === "break" ? (
            <div key={i} className="h-4" />
          ) : (
            <p key={i} style={{ textIndent: "2em" }} className="mb-4 leading-[1.9]">
              {para.content}
            </p>
          )
        )}
      </div>
    );
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

  if (pageError || !novel) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4">
        <p className="text-[#C4372D]">{pageError || "小说不存在"}</p>
        <Button variant="secondary" onClick={() => router.push("/novels")}>
          返回列表
        </Button>
      </div>
    );
  }

  if (versions.length === 0 || activeVersionId === null) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4">
        <p className="text-[#7E756D]">未找到可用版本，请稍后重试。</p>
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
          <div className="flex flex-wrap items-center justify-end gap-2">
            <div className="w-[180px]">
              <Select
                value={String(activeVersionId ?? "")}
                onValueChange={(nextValue) => {
                  const nextId = Number(nextValue);
                  if (!Number.isFinite(nextId) || nextId === activeVersionId) return;
                  if (annotations.length > 0) {
                    setPendingVersionId(nextId);
                    return;
                  }
                  setError(null);
                  setActiveVersionId(nextId);
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
              提交修改建议（{annotations.length}）
            </Button>
            <div className="inline-flex h-9 items-center gap-1.5 rounded-full border border-[#E5DED7] bg-[#FFFDFB] px-3 text-sm font-medium text-[#6F665F]">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  displayNovelStatus === "completed"
                    ? "bg-[#18864B]"
                    : displayNovelStatus === "failed"
                    ? "bg-[#C4372D]"
                    : getNovelStatusVariant(displayNovelStatus) === "warning"
                    ? "bg-[#D08A10]"
                    : "bg-[#8E8379]"
                }`}
              />
              {STATUS_MAP[displayNovelStatus]?.label || formatNovelStatus(displayNovelStatus)}
            </div>
            <div className="relative">
              <Button
                variant="secondary"
                size="sm"
                className="h-9 px-3 shadow-none border-[#E5DED7] bg-white hover:bg-[#F8F5F1]"
                onClick={() => setShowExport((prev) => !prev)}
              >
                <Download className="w-4 h-4 mr-2" />
                导出
              </Button>
              {showExport ? (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setShowExport(false)} />
                  <div className="absolute right-0 mt-1.5 w-40 bg-white border border-[#E5DED7] rounded-[12px] shadow-[0_10px_30px_rgba(0,0,0,0.08)] z-20 overflow-hidden">
                    {(["txt", "md", "zip"] as const).map((format) => (
                      <a
                        key={format}
                        href={activeVersionId ? api.getExportUrl(id, format, activeVersionId) : "#"}
                        download
                        className={[
                          "block px-4 py-2.5 text-sm transition-colors",
                          activeVersionId
                            ? "text-[#3A3A3C] hover:bg-[#F6F3EF]"
                            : "text-[#ACA39A] cursor-not-allowed",
                        ].join(" ")}
                        onClick={() => setShowExport(false)}
                      >
                        导出为 .{format}
                      </a>
                    ))}
                  </div>
                </>
              ) : null}
            </div>
            <Link href={`/novels/${id}/progress${activeVersionId ? `?version_id=${activeVersionId}` : ""}`}>
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
            description={
              incrementalOutlineHint
                ? `当前长篇目录按卷逐步生成。第一卷大纲准备完成后会先显示，后续卷会随着任务推进自动补齐，目标共 ${incrementalOutlineHint.targetChapters} 章。`
                : "请稍后刷新，或前往进度页查看任务状态。"
            }
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
                {incrementalOutlineHint && showIncrementalOutlineHint ? (
                  <div className="relative mb-4 rounded-[10px] border border-[#F3D8B3] bg-[#FFF8EE] px-3 py-2.5 text-xs leading-5 text-[#8A5A13]">
                    <button
                      type="button"
                      aria-label="关闭提示"
                      className="absolute right-2 top-2 inline-flex h-6 w-6 items-center justify-center rounded-full text-[#B07B30] transition-colors hover:bg-[#F8E8CF] hover:text-[#8A5A13]"
                      onClick={() => setShowIncrementalOutlineHint(false)}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                    <div className="flex items-start gap-2 pr-6">
                      <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                      <div>
                        长篇目录按卷逐步生成。当前已展示至第 {incrementalOutlineHint.outlinedChapters} 章，后续卷会随着任务推进自动补齐，目标共 {incrementalOutlineHint.targetChapters} 章。
                      </div>
                    </div>
                  </div>
                ) : null}
                <div className="space-y-2 max-h-[60vh] overflow-y-auto pr-1">
                  {groupedVolumes.map((volume) => {
                    const expanded = expandedVolumeNos.has(volume.volumeNo);
                    const panelId = `volume-${volume.volumeNo}-chapters`;
                    return (
                      <div key={volume.volumeNo} className="rounded-[10px] border border-[#E7DDD4] bg-[#FFFCFA]">
                        <button
                          type="button"
                          className="w-full px-3 py-2.5 flex items-center justify-between gap-3 text-left rounded-[10px] hover:bg-[#F8F4EF]"
                          onClick={() => toggleVolumeExpand(volume.volumeNo)}
                          aria-expanded={expanded}
                          aria-controls={panelId}
                        >
                          <span className="min-w-0 inline-flex items-center gap-1.5 text-sm font-medium text-[#3A3A3C]">
                            {expanded ? <ChevronDown className="w-4 h-4 text-[#8E8379]" /> : <ChevronRight className="w-4 h-4 text-[#8E8379]" />}
                            <span className="truncate">
                              第{volume.volumeNo}卷（{volume.startChapter}-{volume.endChapter}章）
                            </span>
                          </span>
                          <span className="shrink-0 text-xs text-[#8E8379]">{volume.completed}/{volume.total}</span>
                        </button>
                        {expanded ? (
                          <div id={panelId} className="space-y-1 border-t border-[#EFE7E0] px-2 pb-2 pt-1.5">
                            {volume.chapters.map((chapter) => (
                              <button
                                key={chapter.chapter_num}
                                onClick={() => setSelectedChapterNum(chapter.chapter_num)}
                                className={[
                                  "w-full text-left px-3 py-2 rounded-lg text-sm transition-all",
                                  selectedChapterNum === chapter.chapter_num
                                    ? "bg-[#F8ECEA] text-[#A52A25] border border-[#EED1CC]"
                                    : "text-[#7E756D] hover:bg-[#F6F3EF] hover:text-[#1F1B18]",
                                ].join(" ")}
                              >
                                <div className="font-medium flex items-center justify-between gap-2">
                                  <span className="inline-flex items-center gap-2">
                                    <span>第 {chapter.chapter_num} 章</span>
                                    {(annotationCountByChapter.get(chapter.chapter_num) || 0) > 0 ? (
                                      <span className="inline-flex min-w-5 h-5 items-center justify-center rounded-full bg-[#C8211B] px-1 text-[11px] font-semibold text-white">
                                        {annotationCountByChapter.get(chapter.chapter_num)}
                                      </span>
                                    ) : null}
                                  </span>
                                  <Badge variant={(CHAPTER_STATUS_MAP[chapter.status] || CHAPTER_STATUS_MAP.pending).variant}>
                                    {(CHAPTER_STATUS_MAP[chapter.status] || CHAPTER_STATUS_MAP.pending).label}
                                  </Badge>
                                </div>
                                <div className="text-xs opacity-70 truncate">
                                  {getDisplayChapterTitle(chapter.chapter_num, chapter.title)}
                                </div>
                              </button>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </Card>
            </aside>

            <div className="lg:col-span-3">
              <Card className="p-8">
                {selectedChapter ? (
                  <article className="max-w-none">
                    <div className="mb-5 flex items-center justify-between gap-3">
                      <p className="text-sm text-[#7E756D]">{selectedWordCount.toLocaleString()} 字</p>
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
                      className="text-[#3A3A3C] text-base leading-relaxed cursor-text"
                    >
                      {renderSelectedChapterContent()}
                    </div>
                    {selectedChapterAnnotations.length ? (
                      <div className="mt-6 rounded-[12px] border border-[#E7DDD4] bg-[#FFFCFA] p-4">
                        <h4 className="text-sm font-medium text-[#3A3A3C]">
                          本章修改建议（{selectedChapterAnnotations.length}）
                        </h4>
                        <div className="mt-3 space-y-2">
                          {selectedChapterAnnotations.map((ann) => (
                            <div
                              key={`ann-list-${ann._index}`}
                              className="rounded-[10px] border border-[#E5DED7] bg-white px-3 py-2"
                            >
                              <div className="flex items-center justify-between gap-2">
                                <p className="text-xs text-[#7E756D]">
                                  类型：{ann.issue_type || "other"} · 优先级：{ann.priority || "should"}
                                </p>
                                <button
                                  className="text-xs text-[#C4372D]"
                                  onClick={() => removeAnnotation(ann._index)}
                                >
                                  删除
                                </button>
                              </div>
                              {ann.selected_text ? (
                                <p className="mt-1 text-xs text-[#7E756D] line-clamp-2">“{ann.selected_text}”</p>
                              ) : null}
                              <p className="mt-1 text-xs text-[#3A3A3C]">{ann.instruction}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                    {(typeof selectedChapter.language_quality_score === "number" || selectedLanguageReport) ? (
                      <div className="mt-6 border-t border-[rgba(60,60,67,0.12)] pt-4 text-xs text-[#7E756D]">
                        语言质量：{typeof selectedChapter.language_quality_score === "number"
                          ? (selectedChapter.language_quality_score * 10).toFixed(1)
                          : "-"} / 10
                        {selectedLanguageReport ? (
                          <p className="mt-2 text-[#7E756D] whitespace-pre-wrap">{selectedLanguageReport}</p>
                        ) : null}
                      </div>
                    ) : null}
                    <p className="mt-4 text-xs text-[#8E8379]">
                      提示：选中正文片段后会出现冒泡编辑器，添加后将挂在该片段并计入章节目录气泡。
                    </p>
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

      {selectionDraft && selectionBubble ? (
        <div
          className="fixed z-40"
          style={{ left: `${selectionBubble.left}px`, top: `${selectionBubble.top}px`, width: "360px" }}
        >
          <div
            ref={selectionBubbleRef}
            className="rounded-[14px] border border-[#E7DDD4] bg-white shadow-[0_12px_30px_rgba(31,27,24,0.14)] p-3"
          >
            <p className="text-xs text-[#8E8379]">第 {selectionDraft.chapter_num} 章</p>
            <p className="mt-1 text-xs text-[#5E5650] line-clamp-3">“{selectionDraft.selected_text}”</p>
            <div className="grid grid-cols-2 gap-2 mt-2">
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
              placeholder="输入修改方向，例如：伏笔提前、语气更克制、人物动作要与前文一致。"
              className="w-full min-h-[96px] rounded-md border border-[#E5DED7] bg-white p-2 text-xs outline-none mt-2"
            />
            <div className="mt-2 flex items-center justify-end gap-2">
              <Button
                variant="secondary"
                size="sm"
                className="h-8 px-3"
                onClick={clearSelectionDraft}
              >
                取消
              </Button>
              <Button
                size="sm"
                className="h-8 px-3"
                onClick={addAnnotation}
                disabled={!instructionDraft.trim()}
              >
                添加建议
              </Button>
            </div>
          </div>
        </div>
      ) : null}
      <ConfirmModal
        open={pendingVersionId !== null}
        onClose={() => setPendingVersionId(null)}
        onConfirm={() => {
          if (pendingVersionId === null) return;
          setAnnotations([]);
          clearSelectionDraft();
          setError(null);
          setActiveVersionId(pendingVersionId);
          setPendingVersionId(null);
        }}
        title="切换版本"
        message="切换版本会清空当前未提交的修改建议，是否继续？"
        confirmText="继续切换"
        confirmVariant="primary"
      />
    </main>
  );
}
