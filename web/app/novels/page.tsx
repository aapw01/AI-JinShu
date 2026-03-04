"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { BookOpen, Filter, RefreshCw, Trash2 } from "lucide-react";
import { api, AuthUser, Novel, getErrorMessage } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";
import { ConfirmModal } from "@/components/ui/Modal";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { formatNovelStatus } from "@/lib/display";

type FilterStatus = "all" | "draft" | "generating" | "completed" | "failed";

type AdminUserOption = {
  value: string;
  label: string;
};

const STATUS_MAP: Record<string, { label: string; variant: "default" | "success" | "warning" | "error" | "info" }> = {
  draft: { label: "草稿", variant: "default" },
  generating: { label: "生成中", variant: "warning" },
  completed: { label: "已完成", variant: "success" },
  failed: { label: "失败", variant: "error" },
};

const GENRE_LABELS: Record<string, string> = {
  xuanhuan: "玄幻",
  yanqing: "言情",
  xuanyi: "悬疑",
  kehuan: "科幻",
  lishi: "历史",
  wuxia: "武侠",
  dushi: "都市",
};

const STYLE_LABELS: Record<string, string> = {
  "tomato-hot": "番茄爆款节奏",
  "web-power": "热血爽文",
  "web-emotion": "情绪爽文",
  "mystery-thriller": "悬疑惊悚",
  literary: "细腻唯美",
  "web-novel": "网文通用",
};

export default function NovelsPage() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterStatus>("all");
  const [deleteTarget, setDeleteTarget] = useState<Novel | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [viewer, setViewer] = useState<AuthUser | null>(null);
  const [adminUsers, setAdminUsers] = useState<AdminUserOption[]>([]);
  const [selectedUserUuid, setSelectedUserUuid] = useState("");
  const [onlyMine, setOnlyMine] = useState(false);

  const isAdmin = viewer?.role === "admin";

  useEffect(() => {
    loadViewer();
  }, []);

  useEffect(() => {
    if (!viewer) return;
    void loadNovels();
  }, [viewer, selectedUserUuid, onlyMine]);

  useEffect(() => {
    if (viewer?.role !== "admin") return;
    void loadAdminUsers();
  }, [viewer]);

  const loadViewer = async () => {
    try {
      const res = await api.me();
      setViewer(res.user);
    } catch {
      setViewer(null);
    }
  };

  const loadAdminUsers = async () => {
    try {
      const rows = await api.getAdminUsers();
      setAdminUsers(rows.map((row) => ({ value: row.uuid, label: row.email })));
    } catch {
      setAdminUsers([]);
    }
  };

  const loadNovels = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await api.listNovels(
        isAdmin
          ? {
              user_uuid: onlyMine ? undefined : selectedUserUuid || undefined,
              only_mine: onlyMine,
            }
          : undefined
      );
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
      setDeleteError(null);
      await api.deleteNovel(deleteTarget.id);
      setNovels(novels.filter((n) => n.id !== deleteTarget.id));
      setDeleteTarget(null);
    } catch (err) {
      setDeleteError(getErrorMessage(err, "删除失败，请稍后重试"));
    } finally {
      setDeleting(false);
    }
  };

  const filteredNovels = novels.filter((n) => {
    if (filter === "all") return true;
    return n.status === filter;
  });

  const parseServerTime = (dateStr: string) => {
    const raw = (dateStr || "").trim();
    if (!raw) return new Date(NaN);
    // Backend commonly returns UTC-naive ISO strings; treat them as UTC.
    const hasTz = /([zZ]|[+\-]\d{2}:\d{2})$/.test(raw);
    return new Date(hasTz ? raw : `${raw}Z`);
  };

  const formatDateTime = (dateStr: string) => {
    const date = parseServerTime(dateStr);
    if (Number.isNaN(date.getTime())) return dateStr;
    const formatter = new Intl.DateTimeFormat("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    const formatted = formatter.format(date);
    return formatted.replace(/\//g, "年").replace(",", "").replace(/^(\d{4})年(\d{2})年(\d{2})/, "$1年$2月$3日");
  };

  return (
    <main className="min-h-screen bg-[#F4F3F1]">
      <div className="max-w-[1400px] mx-auto px-4 py-8">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-[#C8211B]/10 flex items-center justify-center">
              <BookOpen className="w-5 h-5 text-[#C8211B]" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-[#1F1B18]">我的作品</h1>
              <p className="text-sm text-[#8B8379]">管理小说项目、查看创作状态与更新时间</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void loadNovels()}
              disabled={loading}
              className="h-9 px-3 text-sm rounded-[10px] border border-[#DDD8D3] text-[#3E3833] hover:bg-[#F2EEEA] inline-flex items-center gap-1.5 disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
              刷新
            </button>
          </div>
        </div>

        <div className="rounded-2xl border border-[#E6DED6] bg-white p-3 mb-6 shadow-[0_2px_8px_rgba(31,27,24,0.04)]">
          <div className="flex flex-wrap items-center gap-2.5">
            {isAdmin ? (
              <>
                <span className="px-2 text-xs font-medium tracking-wide text-[#8E8379]">查看范围</span>
                <div className="w-full sm:w-[240px]">
                  <Select
                    value={selectedUserUuid}
                    onChange={(e) => {
                      setSelectedUserUuid(e.target.value);
                      setOnlyMine(false);
                    }}
                    options={[
                      { value: "", label: "全部用户" },
                      ...adminUsers,
                    ]}
                    disabled={onlyMine}
                    className="h-9 px-3 py-2 text-sm bg-white"
                  />
                </div>
                <div className="inline-flex items-center rounded-[11px] border border-[#E5DED7] bg-white p-1">
                  <button
                    type="button"
                    className={`h-8 px-3 rounded-[8px] text-sm transition-colors ${
                      !onlyMine ? "bg-[#F8ECEA] text-[#A52A25]" : "text-[#6F665F] hover:bg-[#F6F3EF]"
                    }`}
                    onClick={() => {
                      setOnlyMine(false);
                    }}
                  >
                    全部作品
                  </button>
                  <button
                    type="button"
                    className={`h-8 px-3 rounded-[8px] text-sm transition-colors ${
                      onlyMine ? "bg-[#F8ECEA] text-[#A52A25]" : "text-[#6F665F] hover:bg-[#F6F3EF]"
                    }`}
                    onClick={() => {
                      setOnlyMine(true);
                      setSelectedUserUuid("");
                    }}
                  >
                    我的作品
                  </button>
                </div>
                <div className="hidden xl:block h-6 w-px bg-[#E5DED7] mx-1" />
              </>
            ) : null}
            <div className="shrink-0 px-2 text-[#7E756D]">
              <Filter className="w-4 h-4" />
            </div>
            {(["all", "draft", "generating", "completed", "failed"] as FilterStatus[]).map((status) => (
              <button
                key={status}
                onClick={() => setFilter(status)}
                className={`px-4 py-2 text-sm rounded-full border whitespace-nowrap transition-all ${
                  filter === status
                    ? "bg-[#C8211B] text-white border-[#C8211B]"
                    : "bg-white text-[#7E756D] border-[rgba(60,60,67,0.14)] hover:text-[#1F1B18] hover:border-[rgba(60,60,67,0.28)]"
                }`}
              >
                {status === "all" ? "全部" : STATUS_MAP[status]?.label || status}
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Spinner />
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
                    <h3 className="font-semibold text-[#1F1B18] line-clamp-1 pr-3">{novel.title}</h3>
                    <Badge variant={STATUS_MAP[novel.status]?.variant || "default"}>
                      {STATUS_MAP[novel.status]?.label || formatNovelStatus(novel.status)}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-2 text-sm text-[#7E756D] mb-1">
                    {novel.genre && (
                      <>
                        <span>{GENRE_LABELS[novel.genre] || novel.genre}</span>
                        <span>·</span>
                      </>
                    )}
                    {novel.style && (
                      <>
                        <span>{STYLE_LABELS[novel.style] || novel.style}</span>
                        <span>·</span>
                      </>
                    )}
                    <span>{formatDateTime(novel.updated_at || novel.created_at)}</span>
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
        onClose={() => { setDeleteTarget(null); setDeleteError(null); }}
        onConfirm={handleDelete}
        title="删除小说"
        message={deleteError ? `${deleteError}\n\n确定要删除「${deleteTarget?.title}」吗？此操作无法撤销。` : `确定要删除「${deleteTarget?.title}」吗？此操作无法撤销。`}
        confirmText="删除"
        loading={deleting}
      />
    </main>
  );
}
