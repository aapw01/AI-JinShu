"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { BookOpen, CircleAlert, LoaderCircle, RotateCcw, Sparkles, WandSparkles, Zap } from "lucide-react";
import { api, Novel } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { Badge } from "@/components/ui/Badge";

type FilterStatus = "all" | "generating" | "completed" | "failed";

const STATUS_MAP: Record<string, { label: string; variant: "default" | "success" | "warning" | "error" | "info" }> = {
  draft: { label: "草稿", variant: "default" },
  generating: { label: "生成中", variant: "warning" },
  completed: { label: "已完成", variant: "success" },
  failed: { label: "失败", variant: "error" },
};

const STEPS = [
  { id: 1, title: "创意设定" },
  { id: 2, title: "语言" },
  { id: 3, title: "类型" },
  { id: 4, title: "风格" },
  { id: 5, title: "参数配置" },
];

const LANGUAGES = [
  { value: "zh", label: "中文" },
  { value: "en", label: "English" },
  { value: "es", label: "Español" },
  { value: "fr", label: "Français" },
  { value: "de", label: "Deutsch" },
  { value: "pt", label: "Português" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
  { value: "ar", label: "العربية" },
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
  { id: "short", label: "短篇（练笔）", description: "12-30章", chapter_target: 20 },
  { id: "medium", label: "中篇（可完结）", description: "30-80章", chapter_target: 50 },
  { id: "long", label: "长篇（平台主流）", description: "80-220章", chapter_target: 120 },
  { id: "epic", label: "超长篇（强连载）", description: "220-500章", chapter_target: 300 },
  { id: "serial", label: "连载（持续更新）", description: "500+章", chapter_target: 500 },
];

const DEFAULT_AUDIENCES = [
  { id: "general", label: "大众读者", description: "适合广泛读者" },
  { id: "male-web", label: "男频网文", description: "升级成长、冲突强" },
  { id: "female-web", label: "女频网文", description: "关系推进、情绪浓度高" },
  { id: "hardcore-serial", label: "重度追更", description: "接受超长连载与密集卡点" },
];

const METHODS = [
  { value: "three_act", label: "三幕式结构" },
  { value: "hero_journey", label: "英雄之旅" },
  { value: "free", label: "自由发挥" },
];

interface FormData {
  title: string;
  idea: string;
  language: string;
  genre: string;
  style: string;
  audience: string;
  length: string;
  chapterTarget: number;
  method: string;
}

interface NovelMetrics {
  completed: number;
  total: number;
  percent: number;
  words: number;
  quality: number;
}

export default function Home() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const [novels, setNovels] = useState<Novel[]>([]);
  const [novelsLoading, setNovelsLoading] = useState(true);
  const [novelsError, setNovelsError] = useState<string | null>(null);
  const [historyFilter, setHistoryFilter] = useState<FilterStatus>("all");
  const [metricsById, setMetricsById] = useState<Record<string, NovelMetrics>>({});

  const [form, setForm] = useState<FormData>({
    title: "",
    idea: "",
    language: "zh",
    genre: "",
    style: "tomato-hot",
    audience: "general",
    length: "medium",
    chapterTarget: 50,
    method: "three_act",
  });
  const [genres, setGenres] = useState(DEFAULT_GENRES);
  const [styles, setStyles] = useState(DEFAULT_STYLES);
  const [lengths, setLengths] = useState(DEFAULT_LENGTHS);
  const [audiences, setAudiences] = useState(DEFAULT_AUDIENCES);

  const selectedLength = useMemo(
    () => lengths.find((x) => x.id === form.length) || lengths[0],
    [lengths, form.length]
  );
  const selectedStyle = useMemo(
    () => styles.find((x) => x.id === form.style) || styles[0],
    [styles, form.style]
  );

  const inProgressNovels = useMemo(() => novels.filter((n) => n.status === "generating"), [novels]);
  const filteredNovels = useMemo(
    () => novels.filter((n) => historyFilter === "all" || n.status === historyFilter),
    [novels, historyFilter]
  );

  useEffect(() => {
    (async () => {
      try {
        const presets = await api.getPresets();
        const g = (presets.genres || []).map((x) => ({ id: x.id, label: x.label, desc: x.description || "" }));
        const s = (presets.styles || []).map((x) => ({
          id: x.id,
          label: x.label,
          desc: x.description || "",
          strategy: x.strategy || "web-novel",
        }));
        const l = (presets.lengths || []).map((x) => ({
          id: x.id,
          label: x.label,
          description: x.description || "",
          chapter_target: x.chapter_target || 50,
        }));
        const a = (presets.audiences || []).map((x) => ({ id: x.id, label: x.label, description: x.description || "" }));
        if (g.length) setGenres(g);
        if (s.length) setStyles(s);
        if (l.length) setLengths(l);
        if (a.length) setAudiences(a);
      } catch {
        // fallback to defaults
      }
    })();
  }, []);

  useEffect(() => {
    let disposed = false;
    const loadNovels = async () => {
      try {
        setNovelsLoading(true);
        setNovelsError(null);
        const list = await api.listNovels();
        if (!disposed) setNovels(list);
      } catch {
        if (!disposed) setNovelsError("生成历史加载失败");
      } finally {
        if (!disposed) setNovelsLoading(false);
      }
    };
    loadNovels();
    const timer = setInterval(loadNovels, 15000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    const targets = novels.slice(0, 12);
    if (!targets.length) {
      setMetricsById({});
      return;
    }

    (async () => {
      const entries = await Promise.all(
        targets.map(async (novel) => {
          try {
            const [progressRes, chaptersRes] = await Promise.allSettled([
              api.getChapterProgress(novel.id),
              api.getChapters(novel.id),
            ]);

            const progress = progressRes.status === "fulfilled" ? progressRes.value : [];
            const chapters = chaptersRes.status === "fulfilled" ? chaptersRes.value : [];

            const completed = progress.length
              ? progress.filter((c) => c.status === "completed").length
              : chapters.filter((c) => Boolean(c.content)).length;
            const total =
              progress.length ||
              Number((novel.config as { chapter_target?: number } | undefined)?.chapter_target) ||
              Math.max(...chapters.map((c) => c.chapter_num), 1);
            const percent = Math.min(100, Math.round((completed / Math.max(total, 1)) * 100));
            const words = chapters.reduce((sum, c) => sum + (c.word_count || 0), 0) || completed * 1800;
            const qualityScores = chapters
              .map((c) => c.language_quality_score)
              .filter((x): x is number => typeof x === "number");
            const quality = qualityScores.length
              ? Math.round(((qualityScores.reduce((a, b) => a + b, 0) / qualityScores.length) * 10) * 10) / 10
              : fallbackQuality(novel.id, novel.status);
            return [novel.id, { completed, total, percent, words, quality }] as const;
          } catch {
            return [novel.id, { completed: 0, total: 1, percent: 0, words: 0, quality: fallbackQuality(novel.id, novel.status) }] as const;
          }
        })
      );

      if (!disposed) setMetricsById(Object.fromEntries(entries));
    })();

    return () => {
      disposed = true;
    };
  }, [novels]);

  useEffect(() => {
    const text = form.idea.trim();
    if (text.length < 8) return;
    if (!form.genre) {
      if (/星际|未来|宇宙|科技|AI|人工智能/i.test(text)) setForm((prev) => ({ ...prev, genre: "kehuan" }));
      if (/江湖|武林|侠|门派/i.test(text)) setForm((prev) => ({ ...prev, genre: "wuxia" }));
      if (/宫廷|朝堂|帝王|古代|史/i.test(text)) setForm((prev) => ({ ...prev, genre: "lishi" }));
      if (/都市|职场|现实|校园/i.test(text)) setForm((prev) => ({ ...prev, genre: "dushi" }));
    }
    if (/爽|升级|逆袭|打脸/i.test(text)) setForm((prev) => ({ ...prev, style: "web-power" }));
    if (/细腻|克制|文笔|诗意/i.test(text)) setForm((prev) => ({ ...prev, style: "literary" }));
  }, [form.idea, form.genre]);

  const canProceed = () => {
    switch (step) {
      case 1:
        return form.title.trim().length > 0;
      case 2:
        return form.language.length > 0;
      case 3:
        return form.genre.length > 0;
      case 4:
        return form.style.length > 0;
      case 5:
        return true;
      default:
        return false;
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
        target_language: form.language,
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
      setSubmitError(err instanceof Error ? err.message : "创建失败");
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
                      <Input
                        label="作品标题"
                        placeholder="给你的故事起一个名字..."
                        value={form.title}
                        onChange={(e) => setForm((prev) => ({ ...prev, title: e.target.value }))}
                      />
                      <Textarea
                        label="一句话创意"
                        placeholder="例如：星际移民飞船上，AI 宣布自己拥有了情感..."
                        rows={2}
                        className="min-h-[96px]"
                        maxLength={200}
                        value={form.idea}
                        onChange={(e) => setForm((prev) => ({ ...prev, idea: e.target.value }))}
                      />
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-[#8E8379]">{form.idea.length}/200</span>
                        {form.idea.trim().length >= 8 ? (
                          <span className="inline-flex items-center gap-1.5 text-[#A52A25]">
                            <WandSparkles className="w-3.5 h-3.5" />
                            智能建议：{genres.find((g) => g.id === form.genre)?.label || "待推荐"} · {styles.find((s) => s.id === form.style)?.label || "待推荐"}
                          </span>
                        ) : null}
                      </div>
                    </div>
                  )}

                  {step === 2 && (
                    <div className="space-y-3">
                      <p className="text-sm font-medium text-[#A52A25]">2. 语言</p>
                      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                        {LANGUAGES.map((lang) => (
                          <button
                            key={lang.value}
                            onClick={() => setForm((prev) => ({ ...prev, language: lang.value }))}
                            className={`px-3 py-2 rounded-lg border text-sm transition-all ${
                              form.language === lang.value
                                ? "bg-[#F8ECEA] border-[#EED1CC] text-[#A52A25]"
                                : "bg-white border-[#DDD8D3] text-[#6B635D]"
                            }`}
                          >
                            {lang.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {step === 3 && (
                    <div className="space-y-3">
                      <p className="text-sm font-medium text-[#A52A25]">3. 故事类型</p>
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

                  {step === 4 && (
                    <div className="space-y-3">
                      <p className="text-sm font-medium text-[#A52A25]">4. 写作风格</p>
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

                  {step === 5 && (
                    <div className="space-y-3">
                      <p className="text-sm font-medium text-[#A52A25]">5. 生成参数</p>
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
                          max={1000}
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
                    {step < 5 ? (
                      <Button className="bg-[#C8211B] hover:bg-[#AD1B16] shadow-none" onClick={() => setStep((prev) => Math.min(5, prev + 1))} disabled={!canProceed()}>
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
                <Badge variant="warning">{inProgressNovels.length}</Badge>
              </div>
              {inProgressNovels.length === 0 ? (
                <p className="text-sm text-[#8E8379]">暂无进行中的任务</p>
              ) : (
                <div className="space-y-2">
                  {inProgressNovels.slice(0, 1).map((novel) => {
                    const m = metricsById[novel.id];
                    return (
                      <Link key={novel.id} href={`/novels/${novel.id}/progress`} className="block rounded-[12px] border border-[#E4DFDA] bg-white p-3">
                        <div className="flex items-center justify-between gap-2">
                          <p className="font-semibold text-[#1F1B18] line-clamp-1">{novel.title}</p>
                          <span className="text-[#1E7ADC] font-semibold">{m?.percent || 0}%</span>
                        </div>
                        <div className="mt-2 h-2 bg-[#EFE9E3] rounded-full overflow-hidden">
                          <div className="h-full bg-[#1E7ADC]" style={{ width: `${m?.percent || 0}%` }} />
                        </div>
                        <p className="text-xs text-[#8E8379] mt-1">
                          正在生成第 {Math.max(1, (m?.completed || 0) + 1)} 章 · {(m?.completed || 0)}/{m?.total || 0} 章
                        </p>
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
                    return (
                      <Link
                        key={novel.id}
                        href={novel.status === "generating" ? `/novels/${novel.id}/progress` : `/novels/${novel.id}`}
                        className="block rounded-[12px] border border-[#E4DFDA] bg-white p-3 hover:bg-[#FCFBFA] transition-colors"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <p className="font-semibold text-[#1F1B18] line-clamp-1">{novel.title}</p>
                          {novel.status === "failed" ? (
                            <span className="inline-flex items-center gap-1 text-[#B22F2A] text-xs font-medium">
                              <RotateCcw className="w-3.5 h-3.5" />
                              重试
                            </span>
                          ) : (
                            <Badge variant={STATUS_MAP[novel.status]?.variant || "default"}>
                              {STATUS_MAP[novel.status]?.label || novel.status}
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
                <span className="text-xs text-[#8E8379]">{step}/5</span>
              </div>
              <div className="grid grid-cols-1 gap-2 text-xs flex-1 min-h-0 overflow-y-auto pr-1">
                <SummaryChip label="标题" value={form.title || "未填写"} />
                <SummaryChip label="创意" value={form.idea || "未填写"} />
                <SummaryChip label="语言" value={LANGUAGES.find((l) => l.value === form.language)?.label || "未选择"} />
                <SummaryChip label="类型" value={genres.find((g) => g.id === form.genre)?.label || "未选择"} />
                <SummaryChip label="风格" value={styles.find((s) => s.id === form.style)?.label || "未选择"} />
                <SummaryChip label="读者" value={audiences.find((a) => a.id === form.audience)?.label || "未选择"} />
                <SummaryChip label="长度" value={lengths.find((l) => l.id === form.length)?.label || "未选择"} />
                <SummaryChip label="目标章节" value={`${form.chapterTarget} 章`} />
                <SummaryChip label="叙事结构" value={METHODS.find((m) => m.value === form.method)?.label || "未选择"} />
              </div>
              <div className="mt-3">
                <div className="h-2 rounded-full bg-[#EFE9E3] overflow-hidden">
                  <div className="h-full bg-[#C8211B]" style={{ width: `${(step / 5) * 100}%` }} />
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

function fallbackQuality(seed: string, status: string) {
  if (status === "draft") return 0;
  const base = Array.from(seed).reduce((sum, c) => sum + c.charCodeAt(0), 0);
  return 82 + (base % 14);
}
