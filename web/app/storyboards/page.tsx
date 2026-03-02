"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Film, PlayCircle, Sparkles } from "lucide-react";
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

export default function StoryboardsPage() {
  const [items, setItems] = useState<StoryboardProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        setItems(await api.listStoryboardProjects());
      } catch {
        setError("加载失败，请稍后重试");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  return (
    <main className="max-w-[1400px] mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-[#1F1B18]">导演分镜项目</h1>
          <p className="text-sm text-[#7E756D] mt-1">仅支持平台内已完结小说，双模板并行输出专业分镜。</p>
        </div>
      </div>

      {loading ? <p className="text-sm text-[#7E756D]">加载中...</p> : null}

      {!loading && error ? (
        <Card className="p-8 text-center">
          <p className="text-[#C4372D] mb-3">{error}</p>
          <Button variant="secondary" onClick={() => window.location.reload()}>重试</Button>
        </Card>
      ) : null}

      {!loading && !error && items.length === 0 ? (
        <Card className="p-8 text-center">
          <Film className="w-10 h-10 text-[#C8211B] mx-auto mb-3" />
          <p className="text-[#3A3A3C]">暂无分镜项目</p>
          <p className="text-sm text-[#8E8379] mt-1">请先到作品详情页，从已完结小说发起“生成导演分镜脚本”。</p>
        </Card>
      ) : null}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {items.map((p) => (
          <Card key={p.id} className="p-5 space-y-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm text-[#8E8379]">项目 #{p.id}</p>
                <h2 className="text-lg font-semibold text-[#1F1B18]">{p.novel_title || `小说 ${p.novel_id}`}</h2>
              </div>
              <span className="text-xs rounded-full border border-[#E5DED7] px-2.5 py-1 bg-[#FFFDFB] text-[#6F665F]">
                {STATUS_TEXT[p.status] || "未知状态"}
              </span>
            </div>

            <div className="text-sm text-[#6F665F] grid grid-cols-2 gap-y-1">
              <span>目标集数：{p.target_episodes}</span>
              <span>单集时长：{p.target_episode_seconds}s</span>
              <span>风格：{p.style_profile || "跟随小说"}</span>
              <span>模板：{p.output_lanes.map((lane) => formatStoryboardLane(lane)).join(" + ")}</span>
            </div>

            <div className="flex items-center gap-2">
              <Link href={`/storyboards/${p.id}`}>
                <Button size="sm" className="h-8 px-3">
                  <PlayCircle className="w-4 h-4 mr-1.5" />
                  打开工作台
                </Button>
              </Link>
              <Link href={`/storyboards/${p.id}`}>
                <Button variant="secondary" size="sm" className="h-8 px-3">
                  <Sparkles className="w-4 h-4 mr-1.5" />
                  查看评分
                </Button>
              </Link>
            </div>
          </Card>
        ))}
      </div>
    </main>
  );
}
