"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { ArrowLeft, ArrowRight, Globe, PenSquare, Sparkles, WandSparkles } from "lucide-react";
import { api, getErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Textarea } from "@/components/ui/Textarea";
import { Select } from "@/components/ui/Select";
import { TopBar } from "@/components/ui/TopBar";
import { ErrorDialog } from "@/components/ui/ErrorDialog";

const STEPS = [
  { id: 1, title: "创意", icon: "lightbulb" },
  { id: 2, title: "语言", icon: "globe" },
  { id: 3, title: "类型", icon: "book" },
  { id: 4, title: "风格", icon: "palette" },
  { id: 5, title: "设置", icon: "settings" },
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
  language: string;
  genre: string;
  style: string;
  audience: string;
  length: string;
  chapterTarget: number;
  method: string;
}

export default function CreatePage() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorDialogOpen, setErrorDialogOpen] = useState(false);
  const [ideaGenerating, setIdeaGenerating] = useState(false);
  const [ideaError, setIdeaError] = useState<string | null>(null);
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
      } catch (e) {
        // Use local defaults when presets API is unavailable.
      }
    })();
  }, []);

  const updateForm = (updates: Partial<FormData>) => {
    setForm((prev) => ({ ...prev, ...updates }));
  };

  const selectedLength = useMemo(
    () => lengths.find((x) => x.id === form.length) || lengths[0],
    [lengths, form.length]
  );
  const selectedStyle = useMemo(
    () => styles.find((x) => x.id === form.style) || styles[0],
    [styles, form.style]
  );

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
      setLoading(true);
      setError(null);
      setErrorDialogOpen(false);

      // Create novel
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

      // Auto-start generation based on selected chapter target.
      const numChapters = Math.max(1, Math.min(1000, Number(form.chapterTarget) || selectedLength?.chapter_target || 50));
      await api.submitGeneration(res.id, numChapters, 1);

      // Go to progress page
      router.push(`/novels/${res.id}/progress`);
    } catch (err) {
      setError(getErrorMessage(err, "创建失败"));
      setErrorDialogOpen(true);
      setLoading(false);
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
      updateForm({ idea: result.editable_framework || result.one_liner });
    } catch (err) {
      setIdeaError(getErrorMessage(err, "AI 创意生成失败"));
    } finally {
      setIdeaGenerating(false);
    }
  };

  return (
    <main className="min-h-screen">
      <TopBar title="创建小说" backHref="/" icon={<ArrowLeft className="w-5 h-5" />} maxWidthClassName="max-w-4xl" />

      <div className="max-w-4xl mx-auto px-4 py-6">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: [0.25, 0.1, 0.25, 1] }}
          className="rounded-[16px] border border-[#DDD8D3] bg-[#FBFAF8] p-6 md:p-8 shadow-none"
        >
        <div className="flex items-center justify-between mb-10">
          {STEPS.map((s, i) => (
            <div key={s.id} className="flex items-center">
              <button
                onClick={() => s.id < step && setStep(s.id)}
                disabled={s.id > step}
                className={`
                  flex flex-col items-center gap-2 transition-all
                  ${s.id === step ? "text-[#C8211B]" : s.id < step ? "text-[#7E756D] cursor-pointer hover:text-[#1F1B18]" : "text-[#B0B0B5] cursor-not-allowed"}
                `}
              >
                <div
                  className={`
                    w-10 h-10 rounded-full flex items-center justify-center text-sm font-medium transition-all
                    ${s.id === step ? "bg-[#F8ECEA] border-2 border-[#C8211B]" : s.id < step ? "bg-[#E9F9EF] border border-[#CDEFD8]" : "bg-white border border-[rgba(60,60,67,0.14)]"}
                  `}
                >
                  {s.id < step ? (
                    <svg className="w-5 h-5 text-[#18864B]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    s.id
                  )}
                </div>
                <span className="text-xs hidden sm:block font-medium">{s.title}</span>
              </button>
              {i < STEPS.length - 1 && (
                <div className={`w-8 sm:w-16 h-0.5 mx-2 ${s.id < step ? "bg-[#CDEFD8]" : "bg-[rgba(60,60,67,0.14)]"}`} />
              )}
            </div>
          ))}
        </div>

        <div className="animate-fade-in">
          {step === 1 && (
            <div className="space-y-6">
              <div className="mb-8">
                <div className="inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs bg-[#F8ECEA] text-[#A52A25] border border-[#EED1CC] mb-3">
                  <PenSquare className="w-3.5 h-3.5" />
                  Story Seed
                </div>
                <h2 className="text-xl font-semibold text-[#1F1B18] mb-1">你的故事从这里开始</h2>
                <p className="text-[#7E756D]">给小说命名，并写下核心创意。</p>
              </div>
              <Input
                label="小说标题"
                placeholder="输入一个吸引人的标题"
                value={form.title}
                onChange={(e) => updateForm({ title: e.target.value })}
                autoFocus
              />
              <div className="flex items-center justify-end -mt-2">
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={handleGenerateIdea}
                  loading={ideaGenerating}
                  disabled={!form.title.trim() || ideaGenerating}
                >
                  <WandSparkles className="w-3.5 h-3.5 mr-1" />
                  AI 生成创意框架
                </Button>
              </div>
              <Textarea
                label="创意描述（可选）"
                placeholder="描述你想要的故事情节、主角设定、世界观等..."
                value={form.idea}
                onChange={(e) => updateForm({ idea: e.target.value })}
                rows={6}
                maxLength={600}
                className="resize-y min-h-[160px] max-h-[360px] overflow-y-auto"
              />
              <div className="flex items-center justify-between text-xs">
                <span className="text-[#8E8379]">{form.idea.length}/600</span>
                {ideaError ? <span className="text-[#B22F2A]">{ideaError}</span> : null}
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-6">
              <div className="mb-8">
                <div className="inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs bg-[#F8ECEA] text-[#A52A25] border border-[#EED1CC] mb-3">
                  <Globe className="w-3.5 h-3.5" />
                  Language
                </div>
                <h2 className="text-xl font-semibold text-[#1F1B18] mb-1">选择创作语言</h2>
                <p className="text-[#7E756D]">AI 会按该语言的母语风格生成文本。</p>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {LANGUAGES.map((lang) => (
                  <button
                    key={lang.value}
                    onClick={() => updateForm({ language: lang.value })}
                    className={`
                      p-4 rounded-xl border text-center transition-all
                      ${form.language === lang.value
                        ? "bg-[#F8ECEA] border-[#EED1CC] text-[#A52A25]"
                        : "bg-white border-[rgba(60,60,67,0.14)] text-[#7E756D] hover:text-[#1F1B18] hover:border-[rgba(60,60,67,0.26)]"
                      }
                    `}
                  >
                    {lang.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-6">
              <div className="mb-8">
                <h2 className="text-xl font-semibold text-[#1F1B18] mb-1">选择小说类型</h2>
                <p className="text-[#7E756D]">类型决定主要叙事节奏与读者期待。</p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                {genres.map((genre) => (
                  <button
                    key={genre.id}
                    onClick={() => updateForm({ genre: genre.id })}
                    className={`
                      p-4 rounded-xl border text-left transition-all
                      ${form.genre === genre.id
                        ? "bg-[#F8ECEA] border-[#EED1CC]"
                        : "bg-white border-[rgba(60,60,67,0.14)] hover:border-[rgba(60,60,67,0.26)]"
                      }
                    `}
                  >
                    <div className={`font-medium mb-1 ${form.genre === genre.id ? "text-[#A52A25]" : "text-[#1F1B18]"}`}>
                      {genre.label}
                    </div>
                    <div className="text-sm text-[#7E756D]">{genre.desc}</div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="space-y-6">
              <div className="mb-8">
                <h2 className="text-xl font-semibold text-[#1F1B18] mb-1">选择写作风格</h2>
                <p className="text-[#7E756D]">风格会直接影响文本节奏与语气。</p>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {styles.map((style) => (
                  <button
                    key={style.id}
                    onClick={() => updateForm({ style: style.id })}
                    className={`
                      p-5 rounded-xl border text-left transition-all
                      ${form.style === style.id
                        ? "bg-[#F8ECEA] border-[#EED1CC]"
                        : "bg-white border-[rgba(60,60,67,0.14)] hover:border-[rgba(60,60,67,0.26)]"
                      }
                    `}
                  >
                    <div className={`font-medium mb-1 ${form.style === style.id ? "text-[#A52A25]" : "text-[#1F1B18]"}`}>
                      {style.label}
                    </div>
                    <div className="text-sm text-[#7E756D]">{style.desc}</div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {step === 5 && (
            <div className="space-y-6">
              <div className="mb-8">
                <div className="inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs bg-[#F8ECEA] text-[#A52A25] border border-[#EED1CC] mb-3">
                  <Sparkles className="w-3.5 h-3.5" />
                  Final Setup
                </div>
                <h2 className="text-xl font-semibold text-[#1F1B18] mb-1">最后的设置</h2>
                <p className="text-[#7E756D]">确定篇幅与叙事结构后即可开始。</p>
              </div>
              <Select
                label="小说长度"
                options={lengths.map((x) => ({ value: x.id, label: `${x.label} ${x.description ? `(${x.description})` : ""}` }))}
                value={form.length}
                onChange={(e) => {
                  const v = e.target.value;
                  const hit = lengths.find((x) => x.id === v);
                  updateForm({ length: v, chapterTarget: hit?.chapter_target || form.chapterTarget });
                }}
              />
              <Input
                label="目标章节数（可改，建议区间见上方）"
                type="number"
                min={1}
                max={1000}
                value={form.chapterTarget}
                onChange={(e) => updateForm({ chapterTarget: Number(e.target.value || 1) })}
              />
              <Select
                label="读者定位"
                options={audiences.map((x) => ({ value: x.id, label: x.label }))}
                value={form.audience}
                onChange={(e) => updateForm({ audience: e.target.value })}
              />
              <Select
                label="叙事结构"
                options={METHODS}
                value={form.method}
                onChange={(e) => updateForm({ method: e.target.value })}
              />

              {/* Summary */}
              <div className="mt-8 p-6 rounded-[12px] bg-[#F6F3EF] border border-[rgba(60,60,67,0.12)]">
                <h3 className="text-sm font-medium text-[#7E756D] mb-4">创作摘要</h3>
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-[#7E756D]">标题</span>
                    <span className="text-[#1F1B18]">{form.title}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[#7E756D]">语言</span>
                    <span className="text-[#1F1B18]">{LANGUAGES.find((l) => l.value === form.language)?.label}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[#7E756D]">类型</span>
                    <span className="text-[#1F1B18]">{genres.find((g) => g.id === form.genre)?.label}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[#7E756D]">风格</span>
                    <span className="text-[#1F1B18]">{styles.find((s) => s.id === form.style)?.label}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[#7E756D]">读者</span>
                    <span className="text-[#1F1B18]">{audiences.find((a) => a.id === form.audience)?.label}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[#7E756D]">目标章节</span>
                    <span className="text-[#1F1B18]">{form.chapterTarget} 章</span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="flex justify-between mt-12">
          <Button
            variant="ghost"
            onClick={() => setStep(step - 1)}
            disabled={step === 1}
            className={step === 1 ? "invisible" : ""}
          >
            <ArrowLeft className="w-4 h-4 mr-2" />
            上一步
          </Button>

          {step < 5 ? (
            <Button onClick={() => setStep(step + 1)} disabled={!canProceed()}>
              下一步
              <ArrowRight className="w-4 h-4 ml-2" />
            </Button>
          ) : (
            <Button onClick={handleSubmit} loading={loading} disabled={!canProceed()}>
              <Sparkles className="w-4 h-4 mr-2" />
              开始创作
            </Button>
          )}
        </div>
        </motion.div>
      </div>
      <ErrorDialog
        open={errorDialogOpen}
        onClose={() => setErrorDialogOpen(false)}
        title="创建失败"
        message={error || "请稍后重试"}
      />
    </main>
  );
}
