"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Film, PlayCircle, RefreshCw } from "lucide-react";
import { api, StoryboardProject } from "@/lib/api";
import { formatStoryboardLane } from "@/lib/display";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

const STATUS_TEXT: Record<string, string> = {
  draft: "草稿",
  generating: "生成中",
  ready: "待定稿",
  finalized: "已定稿",
  failed: "失败",
};

function formatProjectTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

export default function StoryboardsPage() {
  const [items, setItems] = useState<StoryboardProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadProjects = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await api.listStoryboardProjects());
    } catch {
      setError("加载失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  return (
    <main className="min-h-screen bg-[#F4F3F1]">
      <div className="max-w-[1400px] mx-auto px-4 py-8 space-y-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-[#C8211B]/10 flex items-center justify-center">
              <Film className="w-5 h-5 text-[#C8211B]" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-[#1F1B18]">导演分镜</h1>
              <p className="text-sm text-[#8B8379]">仅支持平台内已完结小说，双模板并行输出专业分镜。</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void loadProjects()}
            disabled={loading}
            className="h-9 px-3 text-sm rounded-[10px] border border-[#DDD8D3] text-[#3E3833] hover:bg-[#F2EEEA] inline-flex items-center gap-1.5 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            刷新
          </button>
        </div>

        {loading ? <p className="text-sm text-[#7E756D]">加载中...</p> : null}

        {!loading && error ? (
          <Card className="p-8 text-center rounded-2xl border border-[#E6DED6] bg-white shadow-[0_2px_8px_rgba(31,27,24,0.04)]">
            <p className="text-[#C4372D] mb-3">{error}</p>
            <Button variant="secondary" onClick={() => void loadProjects()}>重试</Button>
          </Card>
        ) : null}

        {!loading && !error && items.length === 0 ? (
          <Card className="p-8 text-center rounded-2xl border border-[#E6DED6] bg-white shadow-[0_2px_8px_rgba(31,27,24,0.04)]">
            <Film className="w-10 h-10 text-[#C8211B] mx-auto mb-3" />
            <p className="text-[#3A3A3C]">暂无分镜项目</p>
            <p className="text-sm text-[#8E8379] mt-1">请先到作品详情页，从已完结小说发起“生成导演分镜脚本”。</p>
          </Card>
        ) : null}

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {items.map((p) => (
            <Card key={p.id} className="p-5 rounded-2xl border border-[#E6DED6] bg-white shadow-[0_2px_8px_rgba(31,27,24,0.04)]">
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm text-[#8E8379]">项目 #{p.id}</p>
                <span className="text-xs rounded-full border border-[#E5DED7] px-2.5 py-1 bg-[#FFFDFB] text-[#6F665F]">
                  {STATUS_TEXT[p.status] || "未知状态"}
                </span>
              </div>

              <h2 className="mt-2 text-lg font-semibold text-[#1F1B18] line-clamp-1">{p.novel_title || `小说 ${p.novel_id}`}</h2>

              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center h-7 px-2.5 rounded-full bg-[#F7F3EF] text-xs text-[#6A615A] border border-[#ECE3DB]">
                  {p.output_lanes.map((lane) => formatStoryboardLane(lane)).join(" + ")}
                </span>
                <span className="inline-flex items-center h-7 px-2.5 rounded-full bg-[#F7F3EF] text-xs text-[#6A615A] border border-[#ECE3DB]">
                  {p.target_episodes} 集
                </span>
                <span className="inline-flex items-center h-7 px-2.5 rounded-full bg-[#F7F3EF] text-xs text-[#6A615A] border border-[#ECE3DB]">
                  {p.target_episode_seconds}s / 集
                </span>
                <span className="inline-flex items-center h-7 px-2.5 rounded-full bg-[#F7F3EF] text-xs text-[#6A615A] border border-[#ECE3DB]">
                  {p.style_profile || "跟随小说风格"}
                </span>
              </div>

              <div className="mt-3 text-xs text-[#8B8379]">
                更新时间：{formatProjectTime(p.updated_at || p.created_at)}
              </div>

              <div className="mt-4 flex items-center gap-2">
                <Link href={`/storyboards/${p.id}`}>
                  <Button size="sm" className="h-8 px-3">
                    <PlayCircle className="w-4 h-4 mr-1.5" />
                    进入工作台
                  </Button>
                </Link>
              </div>
            </Card>
          ))}
        </div>
      </div>
    </main>
  );
}
