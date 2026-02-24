"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { BookOpen, Filter, Plus, Trash2 } from "lucide-react";
import { api, Novel } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { ConfirmModal } from "@/components/ui/Modal";
import { EmptyState } from "@/components/ui/EmptyState";
import { TopBar } from "@/components/ui/TopBar";

type FilterStatus = "all" | "draft" | "generating" | "completed";

const STATUS_MAP: Record<string, { label: string; variant: "default" | "success" | "warning" | "error" | "info" }> = {
  draft: { label: "草稿", variant: "default" },
  generating: { label: "生成中", variant: "warning" },
  completed: { label: "已完成", variant: "success" },
  failed: { label: "失败", variant: "error" },
};

export default function NovelsPage() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterStatus>("all");
  const [deleteTarget, setDeleteTarget] = useState<Novel | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    loadNovels();
  }, []);

  const loadNovels = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.listNovels();
      setNovels(data);
    } catch (err) {
      setError("加载失败，请稍后重试");
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      setDeleting(true);
      await api.deleteNovel(deleteTarget.id);
      setNovels(novels.filter((n) => n.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (err) {
      console.error(err);
    } finally {
      setDeleting(false);
    }
  };

  const filteredNovels = novels.filter((n) => {
    if (filter === "all") return true;
    return n.status === filter;
  });

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr);
    return date.toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  };

  return (
    <main className="min-h-screen">
      <TopBar
        title="我的作品"
        backHref="/"
        icon={<BookOpen className="w-5 h-5" />}
        actions={(
          <Link href="/create">
            <Button>
              <Plus className="w-4 h-4 mr-2" />
              创建小说
            </Button>
          </Link>
        )}
      />

      <div className="max-w-6xl mx-auto px-4 py-8">
        <div className="glass-card p-3 mb-6 flex items-center gap-2 overflow-x-auto">
          <div className="shrink-0 px-2 text-[#6E6E73]">
            <Filter className="w-4 h-4" />
          </div>
          {(["all", "draft", "generating", "completed"] as FilterStatus[]).map((status) => (
            <button
              key={status}
              onClick={() => setFilter(status)}
              className={`px-4 py-2 text-sm rounded-full border whitespace-nowrap transition-all ${
                filter === status
                  ? "bg-[#007AFF] text-white border-[#007AFF]"
                  : "bg-white text-[#6E6E73] border-[rgba(60,60,67,0.14)] hover:text-[#1D1D1F] hover:border-[rgba(60,60,67,0.28)]"
              }`}
            >
              {status === "all" ? "全部" : STATUS_MAP[status]?.label || status}
            </button>
          ))}
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <div className="animate-spin w-8 h-8 border-2 border-[#007AFF] border-t-transparent rounded-full" />
          </div>
        ) : error ? (
          <div className="text-center py-20">
            <p className="text-[#C4372D] mb-4">{error}</p>
            <Button variant="secondary" onClick={loadNovels}>
              重试
            </Button>
          </div>
        ) : filteredNovels.length === 0 ? (
          <EmptyState
            icon={<BookOpen className="w-8 h-8" />}
            title={filter === "all" ? "还没有创建任何小说" : "没有符合条件的小说"}
            description={filter === "all" ? "点击创建，开始你的第一本小说。" : "试试切换筛选条件。"}
            action={
              filter === "all" ? (
                <Link href="/create">
                  <Button>开始创作</Button>
                </Link>
              ) : undefined
            }
          />
        ) : (
          <motion.div
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, ease: [0.25, 0.1, 0.25, 1] }}
            className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
          >
            {filteredNovels.map((novel, idx) => (
              <motion.div
                key={novel.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.28, ease: [0.25, 0.1, 0.25, 1], delay: idx * 0.04 }}
              >
              <Card hover className="group relative p-5">
                <Link href={`/novels/${novel.id}`} className="block">
                  <div className="flex items-start justify-between mb-3">
                    <h3 className="font-semibold text-[#1D1D1F] line-clamp-1 pr-3">{novel.title}</h3>
                    <Badge variant={STATUS_MAP[novel.status]?.variant || "default"}>
                      {STATUS_MAP[novel.status]?.label || novel.status}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-2 text-sm text-[#6E6E73] mb-1">
                    {novel.genre && (
                      <>
                        <span>{novel.genre}</span>
                        <span>·</span>
                      </>
                    )}
                    {novel.style && (
                      <>
                        <span>{novel.style}</span>
                        <span>·</span>
                      </>
                    )}
                    <span>{formatDate(novel.created_at)}</span>
                  </div>
                </Link>
                <div className="absolute top-3 right-3 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      setDeleteTarget(novel);
                    }}
                    className="p-2 text-[#8E8E93] hover:text-[#C4372D] hover:bg-[#FFECEB] rounded-[8px] transition-colors"
                    aria-label="删除"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </Card>
              </motion.div>
            ))}
          </motion.div>
        )}
      </div>

      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDelete}
        title="删除小说"
        message={`确定要删除「${deleteTarget?.title}」吗？此操作无法撤销。`}
        confirmText="删除"
        loading={deleting}
      />
    </main>
  );
}
