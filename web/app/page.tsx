"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { BookOpen, CircleAlert, LoaderCircle, RotateCcw, Sparkles, WandSparkles, Zap } from "lucide-react";
import { api, GenerationTaskItem, getErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { Badge } from "@/components/ui/Badge";
import {
  formatNovelStatus,
  getNovelStatusVariant,
  resolveNovelDisplayStatus,
  shouldOpenNovelProgress,
} from "@/lib/display";
import {
  useHomeGenerationTasks,
  useHomeNovelMetrics,
  useHomeNovels,
  useHomePresets,
} from "@/hooks/use-home-dashboard";

type FilterStatus = "all" | "generating" | "completed" | "failed";

const STATUS_MAP: Record<string, { label: string; variant: "default" | "success" | "warning" | "error" | "info" }> = {
  draft: { label: "草稿", variant: "default" },
  generating: { label: "生成中", variant: "warning" },
  queued: { label: "排队中", variant: "warning" },
  dispatching: { label: "调度中", variant: "warning" },
  awaiting_outline_confirmation: { label: "待确认大纲", variant: "info" },
  paused: { label: "已暂停", variant: "info" },
  completed: { label: "已完成", variant: "success" },
  failed: { label: "失败", variant: "error" },
  cancelled: { label: "已取消", variant: "info" },
};

const STEPS = [
  { id: 1, title: "创意设定" },
  { id: 2, title: "类型" },
  { id: 3, title: "风格" },
  { id: 4, title: "参数配置" },
];

const DEFAULT_GENRES = [
  { id: "xuanhuan", label: "玄幻", desc: "修仙、异能、奇幻世界" },
  { id: "dushi", label: "都市", desc: "现代都市生活与情感" },
  { id: "wuxia", label: "武侠", desc: "江湖恩怨、侠义精神" },
  { id: "kehuan", label: "科幻", desc: "未来科技、星际探索" },
  { id: "yanqing", label: "言情", desc: "浪漫爱情故事" },
  { id: "lishi", label: "历史", desc: "历史背景下的故事" },
  { id: "lingyi", label: "灵异", desc: "悬疑、恐怖、超自然" },
  { id: "youxi", label: "游戏", desc: "游戏世界冒险" },
];

const DEFAULT_STYLES = [
  { id: "tomato-hot", label: "番茄爆款节奏", desc: "黄金三章+密集钩子", strategy: "web-novel" },
  { id: "web-power", label: "热血爽文", desc: "升级打脸、持续兑现", strategy: "web-novel" },
  { id: "web-emotion", label: "情绪爽文", desc: "关系拉扯、情绪起伏", strategy: "web-novel" },
  { id: "literary", label: "文学向", desc: "文笔细腻、慢热表达", strategy: "literary" },
];

const DEFAULT_LENGTHS = [
  { id: "micro", label: "微短篇（极速爽文）", description: "1-5万字, 5-25章", chapter_target: 15 },
  { id: "short", label: "短篇（练笔/快穿）", description: "5-20万字, 25-100章", chapter_target: 60 },
  { id: "medium", label: "中篇（可完结）", description: "20-80万字, 100-400章", chapter_target: 200 },
  { id: "long", label: "长篇（平台主力）", description: "80-300万字, 400-1500章", chapter_target: 800 },
  { id: "epic", label: "超长篇（史诗巨著）", description: "300万字+, 1500+章", chapter_target: 2000 },
];

const DEFAULT_AUDIENCES = [
  { id: "general", label: "大众读者", description: "适合广泛读者" },
  { id: "male-web", label: "男频网文", description: "升级成长、冲突强" },
  { id: "female-web", label: "女频网文", description: "关系推进、情绪浓度高" },
  { id: "hardcore-serial", label: "重度追更", description: "接受超长连载与密集卡点" },
];

const METHODS = [
  { value: "three_act", label: "三幕式（起承转决战）" },
  { value: "hero_journey", label: "英雄之旅（召唤试炼归来）" },
  { value: "qichengzhuanhe", label: "起承转合（东方四段递进）" },
  { value: "dual_line", label: "双线并行（主线副线交叉）" },
  { value: "mystery_puzzle", label: "悬疑拼图（线索逐章揭示）" },
  { value: "free", label: "自由发挥（AI自适应结构）" },
];

interface FormData {
  title: string;
  idea: string;
  genre: string;
  style: string;
  audience: string;
  length: string;
  chapterTarget: number;
  method: string;
}

function formatGenerationCardLine(task?: GenerationTaskItem | null) {
  if (!task?.total_chapters) return null;
  const current = task.current_chapter || 0;
  const total = task.total_chapters || 0;
  switch (task.status) {
    case "paused":
      return `已暂停，将从第 ${current} / ${total} 章继续`;
    case "failed":
      return `生成失败，停在第 ${current} / ${total} 章`;
    case "cancelled":
      return `任务已取消，停在第 ${current} / ${total} 章`;
    case "completed":
      return `生成完成，共 ${total} 章`;
    default:
      return `正在生成第 ${current} / ${total} 章`;
  }
}

export default function Home() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [ideaGenerating, setIdeaGenerating] = useState(false);
  const [ideaError, setIdeaError] = useState<string | null>(null);
  const [historyFilter, setHistoryFilter] = useState<FilterStatus>("all");

  const [form, setForm] = useState<FormData>({
    title: "",
    idea: "",
    genre: "",
    style: "",
    audience: "general",
    length: "short",
    chapterTarget: 60,
    method: "three_act",
  });

  const { data: presetsData } = useHomePresets();
  const {
    data: novels = [],
    isLoading: novelsLoading,
    error: novelsQueryError,
  } = useHomeNovels();
  const { data: metricsById = {} } = useHomeNovelMetrics(novels);
  const { data: activeGenerationByNovelId = {} } = useHomeGenerationTasks(novels);

  const genres = useMemo(() => {
    const items = (presetsData?.genres || []).map((item) => ({ id: item.id, label: item.label, desc: item.description || "" }));
    return items.length ? items : DEFAULT_GENRES;
  }, [presetsData]);

  const styles = useMemo(() => {
    const items = (presetsData?.styles || []).map((item) => ({
      id: item.id,
      label: item.label,
      desc: item.description || "",
      strategy: item.strategy || "web-novel",
    }));
    return items.length ? items : DEFAULT_STYLES;
  }, [presetsData]);

  const lengths = useMemo(() => {
    const items = (presetsData?.lengths || []).map((item) => ({
      id: item.id,
      label: item.label,
      description: item.description || "",
      chapter_target: item.chapter_target || 50,
    }));
    return items.length ? items : DEFAULT_LENGTHS;
  }, [presetsData]);

  const audiences = useMemo(() => {
    const items = (presetsData?.audiences || []).map((item) => ({
      id: item.id,
      label: item.label,
      description: item.description || "",
    }));
    return items.length ? items : DEFAULT_AUDIENCES;
  }, [presetsData]);

  const novelsError = novelsQueryError ? getErrorMessage(novelsQueryError, "生成历史加载失败") : null;

  const selectedLength = useMemo(
    () => lengths.find((x) => x.id === form.length) || lengths[0],
    [lengths, form.length]
  );
  const selectedStyle = useMemo(
    () => styles.find((x) => x.id === form.style) || styles[0],
    [styles, form.style]
  );

  const inProgressNovels = useMemo(() => novels.filter((n) => n.status === "generating"), [novels]);
  const inProgressCards = useMemo(
    () =>
      inProgressNovels.filter((novel) => {
        const task = activeGenerationByNovelId[novel.id];
        return Boolean(task && shouldOpenNovelProgress(task.status));
      }),
    [activeGenerationByNovelId, inProgressNovels]
  );
  const filteredNovels = useMemo(
    () =>
      novels.filter((novel) => {
        const displayStatus = resolveNovelDisplayStatus(novel.status, activeGenerationByNovelId[novel.id]);
        if (historyFilter === "all") return true;
        if (historyFilter === "generating") return shouldOpenNovelProgress(displayStatus);
        return displayStatus === historyFilter;
      }),
    [activeGenerationByNovelId, historyFilter, novels]
  );

  const canProceed = () => {
    switch (step) {
      case 1:
        return form.title.trim().length > 0;
      case 2:
        return form.genre.length > 0;
      case 3:
        return form.style.length > 0;
      case 4:
        return true;
      default:
        return false;
    }
  };

  const handleGenerateIdea = async () => {
    const title = form.title.trim();
    if (!title || ideaGenerating) return;
    setIdeaGenerating(true);
    setIdeaError(null);
    try {
      const result = await api.generateIdeaFramework({
        title,
        genre: form.genre || undefined,
        style: form.style || undefined,
        strategy: selectedStyle?.strategy || "web-novel",
      });
      setForm((prev) => {
        const updates: Partial<typeof prev> = {
          idea: result.editable_framework || result.one_liner,
        };
        if (result.recommended_genre && !prev.genre) {
          updates.genre = result.recommended_genre;
        }
        if (result.recommended_style) {
          updates.style = result.recommended_style;
        }
        return { ...prev, ...updates };
      });
    } catch (err) {
      setIdeaError(getErrorMessage(err, "AI 创意生成失败"));
    } finally {
      setIdeaGenerating(false);
    }
  };

  const handleSubmit = async () => {
    try {
      setSubmitting(true);
      setSubmitError(null);
      const res = await api.createNovel({
        title: form.title,
        genre: form.genre || undefined,
        style: form.style || undefined,
        audience: form.audience || undefined,
        strategy: selectedStyle?.strategy || "web-novel",
        target_language: "zh",
        config: {
          idea: form.idea,
          length: form.length,
          chapter_target: form.chapterTarget,
          method: form.method,
        },
      });
      const numChapters = Math.max(1, Math.min(1000, Number(form.chapterTarget) || selectedLength?.chapter_target || 50));
      await api.submitGeneration(res.id, numChapters, 1);
      router.push(`/novels/${res.id}/progress`);
    } catch (err) {
      setSubmitError(getErrorMessage(err, "创建失败"));
      setSubmitting(false);
    }
  };

  return (
    <main className="min-h-screen bg-[#F4F3F1]">
      <div className="max-w-[1500px] mx-auto px-3 py-2 xl:h-[75vh] xl:overflow-hidden">
        <div className="grid grid-cols-1 xl:grid-cols-[340px_minmax(0,1fr)_280px] gap-4 items-stretch h-full">
          <motion.section
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.28, ease: [0.25, 0.1, 0.25, 1] }}
            className="h-full order-1 xl:order-2"
          >
            <Card className="p-0 h-full overflow-hidden border border-[#DDD8D3] shadow-none rounded-[16px] bg-[#FBFAF8] flex flex-col">
              <div className="px-5 py-4 border-b border-[#E4DFDA]">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-[#C8211B]" />
                  <h1 className="text-[24px] font-semibold leading-none text-[#1F1B18]">创作工作台</h1>
                  <span className="text-[#C8211B] text-[13px] font-medium ml-2">快速开始</span>
                </div>
              </div>

              <div className="p-5 flex-1 min-h-0 flex flex-col">
                <div className="flex flex-wrap items-center gap-1.5 mb-3 shrink-0">
                  {STEPS.map((s) => (
                    <button
                      key={s.id}
                      onClick={() => setStep(s.id)}
                      className={`px-2.5 py-1 rounded-full text-xs border transition-all ${
                        step === s.id
                          ? "bg-[#C8211B] text-white border-[#C8211B]"
                          : s.id < step
                          ? "bg-[#F8ECEA] text-[#A52A25] border-[#EED1CC]"
                          : "bg-[#FBFAF8] text-[#8E8379] border-[#DDD8D3]"
                      }`}
                    >
                      {s.id}. {s.title}
                    </button>
                  ))}
                </div>

                <div className="rounded-[12px] border border-[#E4DFDA] bg-white px-3 py-3 flex-1 min-h-0 overflow-y-auto">
                  {step === 1 && (
                    <div className="space-y-3">
                      <p className="text-sm font-medium text-[#A52A25]">1. 作品标题与一句话创意</p>
                      <div className="space-y-2">
                        <div className="flex items-center justify-between gap-2">
                          <label className="block text-sm font-medium text-[#3A3A3C]">作品标题</label>
                          <Button
                            type="button"
                            size="sm"
                            variant="secondary"
                            className="h-8 px-3"
                            onClick={handleGenerateIdea}
                            loading={ideaGenerating}
                            disabled={!form.title.trim() || ideaGenerating}
                          >
                            <WandSparkles className="w-3.5 h-3.5 mr-1" />
                            AI 生成创意框架
                          </Button>
                        </div>
                        <Input
                          placeholder="给你的故事起一个名字..."
                          value={form.title}
                          onChange={(e) => setForm((prev) => ({ ...prev, title: e.target.value }))}
                        />
                      </div>
                      <Textarea
                        label="一句话创意"
                        placeholder="例如：星际移民飞船上，AI 宣布自己拥有了情感..."
                        rows={6}
                        className="resize-y min-h-[170px] max-h-[380px]"
                        maxLength={600}
                        value={form.idea}
                        onChange={(e) => setForm((prev) => ({ ...prev, idea: e.target.value }))}
                      />
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-[#8E8379]">{form.idea.length}/600</span>
                        {(() => {
                          if (form.idea.trim().length < 8) return null;
                          const genreLabel = genres.find((g) => g.id === form.genre)?.label;
                          const styleLabel = styles.find((s) => s.id === form.style)?.label;
                          if (!genreLabel && !styleLabel) return null;
                          const parts = [genreLabel, styleLabel].filter(Boolean).join(" · ");
                          return (
                            <span className="inline-flex items-center gap-1.5 text-[#A52A25]">
                              <WandSparkles className="w-3.5 h-3.5" />
                              智能建议：{parts}
                            </span>
                          );
                        })()}
                      </div>
                      {ideaError ? <p className="text-xs text-[#B22F2A]">{ideaError}</p> : null}
                    </div>
                  )}

                  {step === 2 && (
                    <div className="space-y-3">
                      <p className="text-sm font-medium text-[#A52A25]">2. 故事类型</p>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                        {genres.map((genre) => (
                          <button
                            key={genre.id}
                            onClick={() => setForm((prev) => ({ ...prev, genre: genre.id }))}
                            className={`text-left rounded-lg border px-3 py-2 transition-all ${
                              form.genre === genre.id ? "bg-[#F8ECEA] border-[#EED1CC]" : "bg-white border-[#DDD8D3]"
                            }`}
                          >
                            <p className={`font-medium ${form.genre === genre.id ? "text-[#A52A25]" : "text-[#1F1B18]"}`}>{genre.label}</p>
                            <p className="text-xs text-[#7E756D] mt-0.5 line-clamp-1">{genre.desc}</p>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {step === 3 && (
                    <div className="space-y-3">
                      <p className="text-sm font-medium text-[#A52A25]">3. 写作风格</p>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                        {styles.map((style) => (
                          <button
                            key={style.id}
                            onClick={() => setForm((prev) => ({ ...prev, style: style.id }))}
                            className={`text-left rounded-lg border px-3 py-2 transition-all ${
                              form.style === style.id ? "bg-[#F8ECEA] border-[#EED1CC]" : "bg-white border-[#DDD8D3]"
                            }`}
                          >
                            <p className={`font-medium ${form.style === style.id ? "text-[#A52A25]" : "text-[#1F1B18]"}`}>{style.label}</p>
                            <p className="text-xs text-[#7E756D] mt-0.5 line-clamp-1">{style.desc}</p>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {step === 4 && (
                    <div className="space-y-3">
                      <p className="text-sm font-medium text-[#A52A25]">4. 生成参数</p>
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <Select
                          label="小说长度"
                          options={lengths.map((x) => ({ value: x.id, label: `${x.label} ${x.description ? `(${x.description})` : ""}` }))}
                          value={form.length}
                          onChange={(e) => {
                            const hit = lengths.find((x) => x.id === e.target.value);
                            setForm((prev) => ({
                              ...prev,
                              length: e.target.value,
                              chapterTarget: hit?.chapter_target || prev.chapterTarget,
                            }));
                          }}
                        />
                        <Input
                          label="目标章节"
                          type="number"
                          min={1}
                          max={5000}
                          value={form.chapterTarget}
                          onChange={(e) => setForm((prev) => ({ ...prev, chapterTarget: Number(e.target.value || 1) }))}
                        />
                        <Select
                          label="读者定位"
                          options={audiences.map((x) => ({ value: x.id, label: x.label }))}
                          value={form.audience}
                          onChange={(e) => setForm((prev) => ({ ...prev, audience: e.target.value }))}
                        />
                        <Select
                          label="叙事结构"
                          options={METHODS}
                          value={form.method}
                          onChange={(e) => setForm((prev) => ({ ...prev, method: e.target.value }))}
                        />
                      </div>
                    </div>
                  )}
                </div>

                <div className="mt-3 h-[86px] shrink-0 pt-3 border-t border-[#E4DFDA] flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-[#8E8379]">请按步骤填写，系统会自动创建并启动长篇生成。</p>
                  <div className="flex items-center gap-2">
                    <Button variant="ghost" onClick={() => setStep((prev) => Math.max(1, prev - 1))} disabled={step === 1}>
                      上一步
                    </Button>
                    {step < 4 ? (
                      <Button className="bg-[#C8211B] hover:bg-[#AD1B16] shadow-none" onClick={() => setStep((prev) => Math.min(4, prev + 1))} disabled={!canProceed()}>
                        下一步
                      </Button>
                    ) : (
                      <Button className="bg-[#C8211B] hover:bg-[#AD1B16] shadow-none" onClick={handleSubmit} loading={submitting} disabled={!canProceed()}>
                        <Zap className="w-4 h-4 mr-1" />
                        开始生成
                      </Button>
                    )}
                  </div>
                </div>

                {submitError ? (
                  <div className="rounded-lg border border-[#F2C1BB] bg-[#FDECE9] px-3 py-2 text-sm text-[#B22F2A]">{submitError}</div>
                ) : null}
              </div>
            </Card>
          </motion.section>

          <motion.aside
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.28, ease: [0.25, 0.1, 0.25, 1], delay: 0.05 }}
            className="h-full min-h-0 flex flex-col gap-3 order-2 xl:order-1"
          >
            <Card className="p-4 border border-[#DDD8D3] shadow-none rounded-[14px] bg-[#FBFAF8] shrink-0">
              <div className="flex items-center justify-between mb-3">
                <h3 className="font-semibold text-[#1F1B18] flex items-center gap-1.5">
                  <Zap className="w-4 h-4 text-[#1F1B18]" />
                  进行中
                </h3>
                <Badge variant="warning">{inProgressCards.length}</Badge>
              </div>
              {inProgressCards.length === 0 ? (
                <p className="text-sm text-[#8E8379]">暂无进行中的任务</p>
              ) : (
                <div className="space-y-2">
                  {inProgressCards.slice(0, 1).map((novel) => {
                    const m = metricsById[novel.id];
                    const task = activeGenerationByNovelId[novel.id];
                    const progressPercent = Math.round(task?.progress || m?.percent || 0);
                    return (
                      <Link key={novel.id} href={`/novels/${novel.id}/progress`} className="block rounded-[12px] border border-[#E4DFDA] bg-white p-3">
                        <div className="flex items-center justify-between gap-2">
                          <p className="font-semibold text-[#1F1B18] line-clamp-1">{novel.title}</p>
                          <span className="text-[#1E7ADC] font-semibold">{progressPercent}%</span>
                        </div>
                        <div className="mt-2 h-2 bg-[#EFE9E3] rounded-full overflow-hidden">
                          <div className="h-full bg-[#1E7ADC]" style={{ width: `${progressPercent}%` }} />
                        </div>
                        <p className="text-xs text-[#8E8379] mt-1">{formatGenerationCardLine(task)}</p>
                      </Link>
                    );
                  })}
                </div>
              )}
            </Card>

            <Card className="p-0 overflow-hidden border border-[#DDD8D3] shadow-none rounded-[14px] bg-[#FBFAF8] flex-1 min-h-0 flex flex-col">
              <div className="px-4 py-3 border-b border-[#E4DFDA]">
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold text-[#1F1B18]">生成历史</h3>
                  <Link href="/novels" className="text-sm text-[#B22F2A] hover:underline">查看全部</Link>
                </div>
                <div className="mt-2 flex items-center gap-1">
                  {(["all", "generating", "completed", "failed"] as FilterStatus[]).map((status) => (
                    <button
                      key={status}
                      onClick={() => setHistoryFilter(status)}
                      className={`px-2.5 py-1 rounded-full text-xs border ${
                        historyFilter === status
                          ? "bg-[#C8211B] text-white border-[#C8211B]"
                          : "bg-white text-[#6B635D] border-[#DDD8D3]"
                      }`}
                    >
                      {status === "all" ? "全部" : STATUS_MAP[status].label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="p-3 space-y-2 flex-1 min-h-0 overflow-y-auto">
                {novelsLoading ? (
                  <div className="p-8 text-sm text-[#8E8379] flex items-center justify-center gap-2">
                    <LoaderCircle className="w-4 h-4 animate-spin" />
                    加载历史中
                  </div>
                ) : novelsError ? (
                  <div className="p-4 rounded-lg border border-[#F2C1BB] bg-[#FDECE9] text-sm text-[#B22F2A] flex items-center gap-2">
                    <CircleAlert className="w-4 h-4" />
                    {novelsError}
                  </div>
                ) : filteredNovels.length === 0 ? (
                  <div className="p-10 text-center text-[#8E8379] text-sm">
                    <BookOpen className="w-5 h-5 mx-auto mb-2" />
                    当前筛选下暂无作品
                  </div>
                ) : (
                  filteredNovels.slice(0, 12).map((novel) => {
                    const m = metricsById[novel.id];
                    const displayTask = activeGenerationByNovelId[novel.id];
                    const displayStatus = resolveNovelDisplayStatus(novel.status, displayTask);
                    return (
                      <Link
                        key={novel.id}
                        href={shouldOpenNovelProgress(displayStatus) ? `/novels/${novel.id}/progress` : `/novels/${novel.id}`}
                        className="block rounded-[12px] border border-[#E4DFDA] bg-white p-3 hover:bg-[#FCFBFA] transition-colors"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <p className="font-semibold text-[#1F1B18] line-clamp-1">{novel.title}</p>
                          {displayStatus === "failed" ? (
                            <span className="inline-flex items-center gap-1 text-[#B22F2A] text-xs font-medium">
                              <RotateCcw className="w-3.5 h-3.5" />
                              重试
                            </span>
                          ) : (
                            <Badge variant={STATUS_MAP[displayStatus]?.variant || getNovelStatusVariant(displayStatus)}>
                              {STATUS_MAP[displayStatus]?.label || formatNovelStatus(displayStatus)}
                            </Badge>
                          )}
                        </div>

                        <div className="mt-1.5 text-sm text-[#6B635D] flex items-center gap-2">
                          <span>{m?.completed || 0}/{m?.total || 0} 章</span>
                          <span>·</span>
                          <span>{formatWordCount(m?.words || 0)}</span>
                          <span>·</span>
                          <span className="font-medium text-[#2F2A25]">{m?.quality || "-"} 分</span>
                        </div>
                        <p className="text-xs text-[#8E8379] mt-1">{formatRelativeTime(novel.updated_at || novel.created_at)}</p>
                      </Link>
                    );
                  })
                )}
              </div>
            </Card>
          </motion.aside>

          <motion.aside
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.28, ease: [0.25, 0.1, 0.25, 1], delay: 0.08 }}
            className="h-full min-h-0 flex flex-col gap-3 order-3 xl:order-3"
          >
            <Card className="p-3 border border-[#DDD8D3] shadow-none rounded-[14px] bg-[#FBFAF8] h-full min-h-0 flex flex-col">
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-semibold text-[#1F1B18]">填写预览</h3>
                <span className="text-xs text-[#8E8379]">{step}/4</span>
              </div>
              <div className="grid grid-cols-1 gap-2 text-xs flex-1 min-h-0 overflow-y-auto pr-1">
                <SummaryChip label="标题" value={form.title || "未填写"} />
                <SummaryChip label="创意" value={form.idea || "未填写"} />
                <SummaryChip label="类型" value={genres.find((g) => g.id === form.genre)?.label || "未选择"} />
                <SummaryChip label="风格" value={styles.find((s) => s.id === form.style)?.label || "未选择"} />
                <SummaryChip label="读者" value={audiences.find((a) => a.id === form.audience)?.label || "未选择"} />
                <SummaryChip label="长度" value={lengths.find((l) => l.id === form.length)?.label || "未选择"} />
                <SummaryChip label="目标章节" value={`${form.chapterTarget} 章`} />
                <SummaryChip label="叙事结构" value={METHODS.find((m) => m.value === form.method)?.label || "未选择"} />
              </div>
              <div className="mt-3">
                <div className="h-2 rounded-full bg-[#EFE9E3] overflow-hidden">
                  <div className="h-full bg-[#C8211B]" style={{ width: `${(step / 4) * 100}%` }} />
                </div>
                <p className="text-[11px] text-[#8E8379] mt-1">继续填写后可进入下一步。</p>
              </div>
            </Card>
          </motion.aside>
        </div>
      </div>
    </main>
  );
}

function formatRelativeTime(dateStr: string) {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMin = Math.max(1, Math.floor((now - then) / 60000));
  if (diffMin < 60) return `${diffMin} 分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour} 小时前`;
  const diffDay = Math.floor(diffHour / 24);
  return `${diffDay} 天前`;
}

function SummaryChip({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[8px] border border-[#E6E1DC] bg-white px-2.5 py-2">
      <p className="text-[#8E8379]">{label}</p>
      <p className="font-medium text-[#2F2A25] mt-0.5 line-clamp-1">{value}</p>
    </div>
  );
}

function formatWordCount(words: number) {
  if (!words) return "0 字";
  if (words >= 10000) return `${(words / 10000).toFixed(1)} 万字`;
  return `${words.toLocaleString()} 字`;
}
