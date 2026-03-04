"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import {
    Search,
    Filter,
    ShieldCheck,
    ShieldOff,
    UserCog,
    X,
    Save,
    RefreshCw,
    Users,
} from "lucide-react";
import {
    api,
    AdminUserListItem,
    AuthUser,
    getErrorMessage,
} from "@/lib/api";
import {
    formatUserRole,
    formatUserStatus,
    formatPlanKey,
} from "@/lib/display";
import { Select } from "@/components/ui/Select";

// ─── helpers ───────────────────────────────────────────────────────────
function fmtTime(iso?: string | null): string {
    if (!iso) return "-";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "-";
    return d.toLocaleString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function fmtNum(n?: number | null): string {
    if (n === null || n === undefined) return "-";
    if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
}

// ─── status badge ──────────────────────────────────────────────────────
function StatusBadge({ status }: { status: string }) {
    const map: Record<string, { bg: string; text: string }> = {
        active: { bg: "bg-emerald-50 border-emerald-200", text: "text-emerald-700" },
        disabled: { bg: "bg-red-50 border-red-200", text: "text-red-700" },
        pending_activation: { bg: "bg-amber-50 border-amber-200", text: "text-amber-700" },
    };
    const s = map[status] || { bg: "bg-gray-50 border-gray-200", text: "text-gray-600" };
    return (
        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${s.bg} ${s.text}`}>
            {formatUserStatus(status)}
        </span>
    );
}

function VerifiedBadge({ verified }: { verified: boolean }) {
    return verified ? (
        <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
            <ShieldCheck className="w-3.5 h-3.5" />
            已验证
        </span>
    ) : (
        <span className="inline-flex items-center gap-1 text-xs text-amber-500">
            <ShieldOff className="w-3.5 h-3.5" />
            未验证
        </span>
    );
}

// ─── select component ──────────────────────────────────────────────────
function SelectFilter({
    label,
    value,
    onChange,
    options,
}: {
    label: string;
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
}) {
    return (
        <div className="min-w-[132px]">
            <Select
                value={value}
                onValueChange={onChange}
                className="h-9 px-3 py-2 text-sm"
                options={options.map((o) => ({
                    value: o.value,
                    label: `${label}: ${o.label}`,
                }))}
            >
            </Select>
        </div>
    );
}

// ─── quota edit modal ──────────────────────────────────────────────────
function QuotaEditor({
    user,
    onClose,
    onSaved,
}: {
    user: AdminUserListItem;
    onClose: () => void;
    onSaved: () => void;
}) {
    const [maxConcurrent, setMaxConcurrent] = useState(String(user.max_concurrent_tasks ?? 1));
    const [chapterLimit, setChapterLimit] = useState(String(user.monthly_chapter_limit ?? 0));
    const [tokenLimit, setTokenLimit] = useState(String(user.monthly_token_limit ?? 0));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");

    const handleSave = async () => {
        setSaving(true);
        setError("");
        try {
            await api.updateAdminUserQuota(user.uuid, {
                max_concurrent_tasks: parseInt(maxConcurrent) || 1,
                monthly_chapter_limit: parseInt(chapterLimit) || 0,
                monthly_token_limit: parseInt(tokenLimit) || 0,
            });
            onSaved();
        } catch (err) {
            setError(getErrorMessage(err));
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={onClose}>
            <div
                className="w-full max-w-md mx-4 rounded-2xl bg-white border border-[#E6DED6] shadow-[0_24px_64px_rgba(31,27,24,0.2)] p-6"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between mb-5">
                    <h3 className="text-lg font-semibold text-[#1F1B18]">调整限额</h3>
                    <button type="button" onClick={onClose} className="p-1 rounded-lg hover:bg-[#F2EEEA] text-[#8B8379]">
                        <X className="w-5 h-5" />
                    </button>
                </div>

                <p className="text-sm text-[#6B635D] mb-4 truncate">
                    用户：{user.email}
                </p>

                <div className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-[#3E3833] mb-1">最大并发任务数</label>
                        <input
                            type="number"
                            min={1}
                            max={100}
                            value={maxConcurrent}
                            onChange={(e) => setMaxConcurrent(e.target.value)}
                            className="w-full h-10 px-3 rounded-[10px] border border-[#DDD8D3] text-sm text-[#1F1B18] focus:outline-none focus:ring-2 focus:ring-[#C8211B]/20 focus:border-[#C8211B]"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-[#3E3833] mb-1">月章节限额</label>
                        <input
                            type="number"
                            min={0}
                            value={chapterLimit}
                            onChange={(e) => setChapterLimit(e.target.value)}
                            className="w-full h-10 px-3 rounded-[10px] border border-[#DDD8D3] text-sm text-[#1F1B18] focus:outline-none focus:ring-2 focus:ring-[#C8211B]/20 focus:border-[#C8211B]"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-[#3E3833] mb-1">月 Token 限额</label>
                        <input
                            type="number"
                            min={0}
                            value={tokenLimit}
                            onChange={(e) => setTokenLimit(e.target.value)}
                            className="w-full h-10 px-3 rounded-[10px] border border-[#DDD8D3] text-sm text-[#1F1B18] focus:outline-none focus:ring-2 focus:ring-[#C8211B]/20 focus:border-[#C8211B]"
                        />
                    </div>
                </div>

                {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}

                <div className="flex justify-end gap-3 mt-6">
                    <button
                        type="button"
                        onClick={onClose}
                        className="h-9 px-4 text-sm rounded-[10px] border border-[#DDD8D3] text-[#3E3833] hover:bg-[#F2EEEA]"
                    >
                        取消
                    </button>
                    <button
                        type="button"
                        onClick={handleSave}
                        disabled={saving}
                        className="h-9 px-4 text-sm rounded-[10px] bg-[#C8211B] text-white hover:bg-[#AD1B16] disabled:opacity-50 inline-flex items-center gap-1.5"
                    >
                        <Save className="w-4 h-4" />
                        {saving ? "保存中..." : "保存"}
                    </button>
                </div>
            </div>
        </div>
    );
}

// ─── main page ─────────────────────────────────────────────────────────
export default function AdminUsersPage() {
    const router = useRouter();
    const [authUser, setAuthUser] = useState<AuthUser | null>(null);
    const [authLoading, setAuthLoading] = useState(true);

    const [users, setUsers] = useState<AdminUserListItem[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    // filters
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState("");
    const [verifiedFilter, setVerifiedFilter] = useState("");
    const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

    // quota editor
    const [editingUser, setEditingUser] = useState<AdminUserListItem | null>(null);

    // action in‑progress tracking
    const [actionLoading, setActionLoading] = useState<string | null>(null);

    // ── auth check ──
    useEffect(() => {
        let disposed = false;
        (async () => {
            try {
                const res = await api.me();
                if (!disposed) {
                    if (res.user.role !== "admin") {
                        router.replace("/");
                        return;
                    }
                    setAuthUser(res.user);
                }
            } catch {
                if (!disposed) router.replace("/auth/login");
            } finally {
                if (!disposed) setAuthLoading(false);
            }
        })();
        return () => { disposed = true; };
    }, [router]);

    // ── load users ──
    const loadUsers = useCallback(async (q?: string) => {
        setLoading(true);
        setError("");
        try {
            const data = await api.getAdminUsers({
                query: (q ?? search) || undefined,
                status: statusFilter || undefined,
                email_verified: verifiedFilter || undefined,
                limit: 200,
            });
            setUsers(data);
        } catch (err) {
            setError(getErrorMessage(err));
        } finally {
            setLoading(false);
        }
    }, [search, statusFilter, verifiedFilter]);

    useEffect(() => {
        if (!authUser) return;
        loadUsers();
    }, [authUser, statusFilter, verifiedFilter]); // eslint-disable-line react-hooks/exhaustive-deps

    // debounced search
    const onSearchChange = (val: string) => {
        setSearch(val);
        if (searchTimer.current) clearTimeout(searchTimer.current);
        searchTimer.current = setTimeout(() => {
            loadUsers(val);
        }, 300);
    };

    // ── actions ──
    const handleToggleStatus = async (u: AdminUserListItem) => {
        const action = u.status === "disabled" ? "启用" : "禁用";
        if (!confirm(`确定要${action}用户 ${u.email} 吗？`)) return;
        setActionLoading(u.uuid);
        try {
            if (u.status === "disabled") {
                await api.enableAdminUser(u.uuid);
            } else {
                await api.disableAdminUser(u.uuid);
            }
            await loadUsers();
        } catch (err) {
            alert(getErrorMessage(err));
        } finally {
            setActionLoading(null);
        }
    };

    // ── render ──
    if (authLoading) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-[#F4F3F1]">
                <div className="animate-spin w-8 h-8 border-2 border-[#C8211B] border-t-transparent rounded-full" />
            </div>
        );
    }

    if (!authUser || authUser.role !== "admin") return null;

    return (
        <main className="min-h-screen bg-[#F4F3F1]">
            <div className="max-w-[1400px] mx-auto px-4 py-8">
                {/* header */}
                <div className="flex items-center justify-between mb-6">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-xl bg-[#C8211B]/10 flex items-center justify-center">
                            <Users className="w-5 h-5 text-[#C8211B]" />
                        </div>
                        <div>
                            <h1 className="text-xl font-bold text-[#1F1B18]">用户管理</h1>
                            <p className="text-sm text-[#8B8379]">管理平台用户、查看状态与调整限额</p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => loadUsers()}
                        disabled={loading}
                        className="h-9 px-3 text-sm rounded-[10px] border border-[#DDD8D3] text-[#3E3833] hover:bg-[#F2EEEA] inline-flex items-center gap-1.5 disabled:opacity-50"
                    >
                        <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
                        刷新
                    </button>
                </div>

                {/* filters */}
                <div className="flex flex-wrap items-center gap-3 mb-5">
                    <div className="relative flex-1 min-w-[220px] max-w-md">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#8B8379]" />
                        <input
                            type="text"
                            placeholder="搜索用户邮箱..."
                            value={search}
                            onChange={(e) => onSearchChange(e.target.value)}
                            className="w-full h-9 pl-9 pr-3 text-sm rounded-[10px] border border-[#DDD8D3] bg-white placeholder:text-[#B0A89E] focus:outline-none focus:ring-2 focus:ring-[#C8211B]/20 focus:border-[#C8211B]"
                        />
                        {search ? (
                            <button
                                type="button"
                                onClick={() => { setSearch(""); loadUsers(""); }}
                                className="absolute right-2.5 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-[#F0EAE2]"
                            >
                                <X className="w-3.5 h-3.5 text-[#8B8379]" />
                            </button>
                        ) : null}
                    </div>

                    <div className="flex items-center gap-2">
                        <Filter className="w-4 h-4 text-[#8B8379]" />
                        <SelectFilter
                            label="状态"
                            value={statusFilter}
                            onChange={setStatusFilter}
                            options={[
                                { value: "", label: "全部" },
                                { value: "active", label: "正常" },
                                { value: "disabled", label: "已禁用" },
                                { value: "pending_activation", label: "待激活" },
                            ]}
                        />
                        <SelectFilter
                            label="验证"
                            value={verifiedFilter}
                            onChange={setVerifiedFilter}
                            options={[
                                { value: "", label: "全部" },
                                { value: "true", label: "已验证" },
                                { value: "false", label: "未验证" },
                            ]}
                        />
                    </div>
                </div>

                {/* error */}
                {error ? (
                    <div className="mb-4 px-4 py-3 rounded-xl bg-red-50 border border-red-200 text-sm text-red-700">
                        {error}
                    </div>
                ) : null}

                {/* table */}
                <div className="rounded-2xl border border-[#E6DED6] bg-white overflow-hidden shadow-[0_2px_8px_rgba(31,27,24,0.04)]">
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="bg-[#FAF8F5] border-b border-[#E6DED6]">
                                    <th className="text-left px-4 py-3 font-medium text-[#6B635D]">邮箱</th>
                                    <th className="text-left px-4 py-3 font-medium text-[#6B635D]">角色</th>
                                    <th className="text-left px-4 py-3 font-medium text-[#6B635D]">状态</th>
                                    <th className="text-left px-4 py-3 font-medium text-[#6B635D]">邮箱验证</th>
                                    <th className="text-left px-4 py-3 font-medium text-[#6B635D]">上次登录</th>
                                    <th className="text-left px-4 py-3 font-medium text-[#6B635D]">套餐</th>
                                    <th className="text-left px-4 py-3 font-medium text-[#6B635D]">并发</th>
                                    <th className="text-left px-4 py-3 font-medium text-[#6B635D]">月章节</th>
                                    <th className="text-left px-4 py-3 font-medium text-[#6B635D]">月Token</th>
                                    <th className="text-left px-4 py-3 font-medium text-[#6B635D]">注册时间</th>
                                    <th className="text-right px-4 py-3 font-medium text-[#6B635D]">操作</th>
                                </tr>
                            </thead>
                            <tbody>
                                {loading && users.length === 0 ? (
                                    <tr>
                                        <td colSpan={11} className="text-center py-16 text-[#8B8379]">
                                            <div className="flex flex-col items-center gap-2">
                                                <div className="animate-spin w-6 h-6 border-2 border-[#C8211B] border-t-transparent rounded-full" />
                                                加载中...
                                            </div>
                                        </td>
                                    </tr>
                                ) : users.length === 0 ? (
                                    <tr>
                                        <td colSpan={11} className="text-center py-16 text-[#8B8379]">
                                            暂无用户
                                        </td>
                                    </tr>
                                ) : (
                                    users.map((u) => (
                                        <tr
                                            key={u.uuid}
                                            className="border-b border-[#F0EAE2] last:border-b-0 hover:bg-[#FDFCFB] transition-colors"
                                        >
                                            <td className="px-4 py-3">
                                                <span className="font-medium text-[#1F1B18]">{u.email}</span>
                                            </td>
                                            <td className="px-4 py-3 text-[#3E3833]">{formatUserRole(u.role)}</td>
                                            <td className="px-4 py-3"><StatusBadge status={u.status} /></td>
                                            <td className="px-4 py-3"><VerifiedBadge verified={u.email_verified} /></td>
                                            <td className="px-4 py-3 text-[#6B635D] whitespace-nowrap">{fmtTime(u.last_login_at)}</td>
                                            <td className="px-4 py-3 text-[#3E3833]">{formatPlanKey(u.plan_key)}</td>
                                            <td className="px-4 py-3 text-[#3E3833] tabular-nums">{u.max_concurrent_tasks ?? "-"}</td>
                                            <td className="px-4 py-3 text-[#3E3833] tabular-nums">{fmtNum(u.monthly_chapter_limit)}</td>
                                            <td className="px-4 py-3 text-[#3E3833] tabular-nums">{fmtNum(u.monthly_token_limit)}</td>
                                            <td className="px-4 py-3 text-[#6B635D] whitespace-nowrap">{fmtTime(u.created_at)}</td>
                                            <td className="px-4 py-3">
                                                <div className="flex items-center justify-end gap-2">
                                                    {u.role !== "admin" ? (
                                                        <button
                                                            type="button"
                                                            disabled={actionLoading === u.uuid}
                                                            onClick={() => handleToggleStatus(u)}
                                                            className={`h-7 px-2.5 text-xs rounded-lg border inline-flex items-center gap-1 transition-colors disabled:opacity-50 ${u.status === "disabled"
                                                                    ? "border-emerald-200 text-emerald-700 hover:bg-emerald-50"
                                                                    : "border-red-200 text-red-600 hover:bg-red-50"
                                                                }`}
                                                        >
                                                            {u.status === "disabled" ? (
                                                                <>
                                                                    <ShieldCheck className="w-3.5 h-3.5" />
                                                                    启用
                                                                </>
                                                            ) : (
                                                                <>
                                                                    <ShieldOff className="w-3.5 h-3.5" />
                                                                    禁用
                                                                </>
                                                            )}
                                                        </button>
                                                    ) : null}
                                                    <button
                                                        type="button"
                                                        onClick={() => setEditingUser(u)}
                                                        className="h-7 px-2.5 text-xs rounded-lg border border-[#DDD8D3] text-[#3E3833] hover:bg-[#F2EEEA] inline-flex items-center gap-1 transition-colors"
                                                    >
                                                        <UserCog className="w-3.5 h-3.5" />
                                                        限额
                                                    </button>
                                                </div>
                                            </td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>

                    {/* footer */}
                    {users.length > 0 ? (
                        <div className="px-4 py-3 border-t border-[#F0EAE2] bg-[#FAF8F5] text-xs text-[#8B8379]">
                            共 {users.length} 位用户
                        </div>
                    ) : null}
                </div>
            </div>

            {/* quota editor modal */}
            {editingUser ? (
                <QuotaEditor
                    user={editingUser}
                    onClose={() => setEditingUser(null)}
                    onSaved={() => {
                        setEditingUser(null);
                        loadUsers();
                    }}
                />
            ) : null}
        </main>
    );
}
