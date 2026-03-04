"use client";

import { FormEvent, Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Clapperboard, Sparkles } from "lucide-react";
import {
  api,
  getErrorMessage,
  StoryboardLane,
  StoryboardStylePresetItem,
  StoryboardStyleRecommendationItem,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ErrorDialog } from "@/components/ui/ErrorDialog";
import { Select } from "@/components/ui/Select";

function StoryboardCreateForm() {
  const router = useRouter();
  const search = useSearchParams();
  const novelId = search.get("novel_id") || "";

  const [targetEpisodes, setTargetEpisodes] = useState(40);
  const [targetEpisodeSeconds, setTargetEpisodeSeconds] = useState(90);
  const [styleProfile, setStyleProfile] = useState("");
  const [mode, setMode] = useState<"quick" | "professional">("quick");
  const [audienceGoal, setAudienceGoal] = useState("反转");
  const [genreStyleKey, setGenreStyleKey] = useState<string>("");
  const [directorStyleKey, setDirectorStyleKey] = useState<string>("");
  const [copyrightAssertion, setCopyrightAssertion] = useState(false);
  const [lanes, setLanes] = useState<StoryboardLane[]>(["vertical_feed", "horizontal_cinematic"]);
  const [presets, setPresets] = useState<{ genre_styles: StoryboardStylePresetItem[]; director_styles: StoryboardStylePresetItem[] }>({ genre_styles: [], director_styles: [] });
  const [recommendations, setRecommendations] = useState<StoryboardStyleRecommendationItem[]>([]);
  const [loadingRec, setLoadingRec] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorDialogOpen, setErrorDialogOpen] = useState(false);

  const canSubmit = useMemo(() => Boolean(novelId && copyrightAssertion && lanes.length > 0), [novelId, copyrightAssertion, lanes]);

  useEffect(() => {
    if (!novelId) {
      router.replace("/novels");
    }
  }, [novelId, router]);

  useEffect(() => {
    void (async () => {
      try {
        const data = await api.getStoryboardStylePresets();
        setPresets(data);
      } catch {
        // ignore, manual mode still works
      }
    })();
  }, []);

  useEffect(() => {
    if (!novelId) return;
    void (async () => {
      setLoadingRec(true);
      try {
        const rec = await api.getStoryboardStyleRecommendations(novelId);
        setRecommendations(rec.recommendations || []);
        if ((rec.recommendations || []).length > 0) {
          setGenreStyleKey(rec.recommendations[0].genre_style_key);
          setDirectorStyleKey(rec.recommendations[0].director_style_key);
          setStyleProfile(`${rec.recommendations[0].genre_style_label} / ${rec.recommendations[0].director_style_label}`);
        }
      } catch {
        // fallback manual selection
      } finally {
        setLoadingRec(false);
      }
    })();
  }, [novelId]);

  const toggleLane = (lane: StoryboardLane) => {
    setLanes((prev) => {
      if (prev.includes(lane)) return prev.filter((x) => x !== lane);
      return [...prev, lane];
    });
  };

  const pickRecommendation = (item: StoryboardStyleRecommendationItem) => {
    setGenreStyleKey(item.genre_style_key);
    setDirectorStyleKey(item.director_style_key);
    setStyleProfile(`${item.genre_style_label} / ${item.director_style_label}`);
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    try {
      setLoading(true);
      setError(null);
      setErrorDialogOpen(false);
      const project = await api.createStoryboardProject({
        novel_id: novelId,
        target_episodes: targetEpisodes,
        target_episode_seconds: targetEpisodeSeconds,
        style_profile: styleProfile || undefined,
        mode,
        genre_style_key: genreStyleKey || undefined,
        director_style_key: directorStyleKey || undefined,
        auto_style_recommendation: true,
        output_lanes: lanes,
        professional_mode: true,
        audience_goal: audienceGoal,
        copyright_assertion: copyrightAssertion,
      });
      const versions = await api.getVersions(novelId);
      const activeVersion = versions.find((v) => v.is_default) || versions[0];
      if (!activeVersion) {
        throw new Error("小说暂无可用版本，无法生成分镜");
      }
      const gen = await api.generateStoryboard(project.id, activeVersion.id);
      router.push(`/storyboards/${project.id}?task_id=${encodeURIComponent(gen.task_id)}`);
    } catch (err) {
      setError(getErrorMessage(err, "创建失败"));
      setErrorDialogOpen(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-[#F4F3F1]">
      <div className="max-w-[1200px] mx-auto px-4 py-8 space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-[#C8211B]/10 flex items-center justify-center">
              <Clapperboard className="w-5 h-5 text-[#C8211B]" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-[#1F1B18]">创建导演分镜项目</h1>
              <p className="text-sm text-[#8B8379]">小说 ID：{novelId || "未指定"}。系统会先给你 Top3 风格建议，可直接改选。</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => router.back()}
            className="h-9 px-3 text-sm rounded-[10px] border border-[#DDD8D3] text-[#3E3833] hover:bg-[#F2EEEA] inline-flex items-center gap-1.5"
          >
            <ArrowLeft className="w-4 h-4" />
            返回
          </button>
        </div>

        <Card className="p-6 rounded-2xl border border-[#E6DED6] bg-white shadow-[0_2px_8px_rgba(31,27,24,0.04)]">
          <form className="space-y-5" onSubmit={onSubmit}>
          <div className="space-y-2">
            <p className="text-sm text-[#3A3A3C]">创作模式</p>
            <div className="flex gap-2">
              <button type="button" onClick={() => setMode("quick")} className={`h-9 px-3 rounded-full border text-sm ${mode === "quick" ? "border-[#C8211B] bg-[#F8ECEA] text-[#A52A25]" : "border-[#E5DED7] bg-white text-[#6F665F]"}`}>
                快速模式（推荐）
              </button>
              <button type="button" onClick={() => setMode("professional")} className={`h-9 px-3 rounded-full border text-sm ${mode === "professional" ? "border-[#C8211B] bg-[#F8ECEA] text-[#A52A25]" : "border-[#E5DED7] bg-white text-[#6F665F]"}`}>
                专业模式
              </button>
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-sm text-[#3A3A3C]">AI 推荐风格 Top3 {loadingRec ? "（分析中）" : ""}</p>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
              {recommendations.map((item) => (
                <button key={`${item.genre_style_key}-${item.director_style_key}`} type="button" onClick={() => pickRecommendation(item)} className={`text-left rounded-xl border p-3 ${genreStyleKey === item.genre_style_key && directorStyleKey === item.director_style_key ? "border-[#C8211B] bg-[#F8ECEA]" : "border-[#E5DED7] bg-white"}`}>
                  <p className="text-sm font-medium text-[#2D2926]">{item.genre_style_label} × {item.director_style_label}</p>
                  <p className="text-xs text-[#7E756D] mt-1">置信度 {(item.confidence * 100).toFixed(0)}%</p>
                  <p className="text-xs text-[#8E8379] mt-1">{item.reason}</p>
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Select
              label="题材风格"
              value={genreStyleKey}
              onValueChange={setGenreStyleKey}
              className="h-10 px-3 py-2"
              options={[
                { value: "", label: "按推荐自动" },
                ...presets.genre_styles.map((item) => ({ value: item.key, label: item.label })),
              ]}
            />
            <Select
              label="导演风格"
              value={directorStyleKey}
              onValueChange={setDirectorStyleKey}
              className="h-10 px-3 py-2"
              options={[
                { value: "", label: "按推荐自动" },
                ...presets.director_styles.map((item) => ({ value: item.key, label: item.label })),
              ]}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1 text-sm text-[#3A3A3C]">
              <span>目标集数</span>
              <input type="number" min={1} max={200} value={targetEpisodes} onChange={(e) => setTargetEpisodes(Number(e.target.value || 1))} className="w-full border border-[#DDD8D3] rounded-[8px] h-10 px-3 bg-white outline-none focus:border-[#C8211B] focus:ring-2 focus:ring-[#C8211B]/10 transition-colors" />
            </label>
            <label className="space-y-1 text-sm text-[#3A3A3C]">
              <span>单集时长（秒）</span>
              <input type="number" min={30} max={600} value={targetEpisodeSeconds} onChange={(e) => setTargetEpisodeSeconds(Number(e.target.value || 90))} className="w-full border border-[#DDD8D3] rounded-[8px] h-10 px-3 bg-white outline-none focus:border-[#C8211B] focus:ring-2 focus:ring-[#C8211B]/10 transition-colors" />
            </label>
          </div>

          <label className="space-y-1 text-sm text-[#3A3A3C] block">
            <span>风格标签（可选覆盖）</span>
            <input value={styleProfile} onChange={(e) => setStyleProfile(e.target.value)} placeholder="例如：悬疑反转、情绪压迫" className="w-full border border-[#DDD8D3] rounded-[8px] h-10 px-3 bg-white outline-none focus:border-[#C8211B] focus:ring-2 focus:ring-[#C8211B]/10 transition-colors" />
          </label>

          <label className="space-y-1 text-sm text-[#3A3A3C] block">
            <span>观众目标</span>
            <input value={audienceGoal} onChange={(e) => setAudienceGoal(e.target.value)} placeholder="例如：爽感、反转、泪点" className="w-full border border-[#DDD8D3] rounded-[8px] h-10 px-3 bg-white outline-none focus:border-[#C8211B] focus:ring-2 focus:ring-[#C8211B]/10 transition-colors" />
          </label>

          <div>
            <p className="text-sm text-[#3A3A3C] mb-2">输出模板</p>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => toggleLane("vertical_feed")} className={`h-9 px-3 rounded-full border text-sm ${lanes.includes("vertical_feed") ? "border-[#C8211B] bg-[#F8ECEA] text-[#A52A25]" : "border-[#E5DED7] bg-white text-[#6F665F]"}`}>
                竖屏信息流
              </button>
              <button type="button" onClick={() => toggleLane("horizontal_cinematic")} className={`h-9 px-3 rounded-full border text-sm ${lanes.includes("horizontal_cinematic") ? "border-[#C8211B] bg-[#F8ECEA] text-[#A52A25]" : "border-[#E5DED7] bg-white text-[#6F665F]"}`}>
                横屏精品
              </button>
            </div>
          </div>

          <label className="flex items-start gap-2 text-sm text-[#3A3A3C]">
            <input type="checkbox" checked={copyrightAssertion} onChange={(e) => setCopyrightAssertion(e.target.checked)} className="mt-1" />
            <span>我确认拥有该小说改编权或合法授权，并同意用于导演分镜生成。</span>
          </label>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="secondary" type="button" onClick={() => router.back()} className="h-9 px-4">取消</Button>
              <Button type="submit" loading={loading} disabled={!canSubmit} className="h-9 px-4">
                <Sparkles className="w-4 h-4 mr-1.5" />
                创建并开始生成
              </Button>
            </div>
          </form>
        </Card>
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

export default function StoryboardCreatePage() {
  return (
    <Suspense fallback={<main className="max-w-3xl mx-auto px-4 py-8 text-sm text-[#7E756D]">加载中...</main>}>
      <StoryboardCreateForm />
    </Suspense>
  );
}
