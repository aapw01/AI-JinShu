"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Plus, RefreshCw, Save, Settings2, Trash2 } from "lucide-react";

import {
  AdminAdapterType,
  AdminModelProvider,
  AdminModelType,
  AdminRuntimeSettingsResponse,
  AuthUser,
  api,
  getErrorMessage,
} from "@/lib/api";
import { Select } from "@/components/ui/Select";
import { Button } from "@/components/ui/Button";

const MODEL_TYPE_OPTIONS: Array<{ value: AdminModelType; label: string }> = [
  { value: "chat", label: "聊天模型" },
  { value: "embedding", label: "向量模型" },
  { value: "image", label: "图像模型" },
  { value: "video", label: "视频模型" },
];

const ADAPTER_OPTIONS: Array<{ value: AdminAdapterType; label: string }> = [
  { value: "openai_compatible", label: "OpenAI 兼容" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
];

type RuntimeFieldKind = "bool" | "int" | "enum";
type RuntimeField = {
  key: string;
  label: string;
  kind: RuntimeFieldKind;
  description: string;
  options?: Array<{ value: string; label: string }>;
};

const RUNTIME_FIELDS: RuntimeField[] = [
  {
    key: "creation_scheduler_enabled",
    label: "启用任务调度",
    kind: "bool",
    description: "控制调度器是否自动从等待队列派发任务。",
  },
  {
    key: "creation_default_max_concurrent_tasks",
    label: "默认最大并发任务",
    kind: "int",
    description: "用户未单独配置时，每个用户可并发执行的默认任务数。",
  },
  {
    key: "creation_max_dispatch_batch",
    label: "单次最大派发批量",
    kind: "int",
    description: "每轮调度最多派发的任务数量，数值越大吞吐越高。",
  },
  {
    key: "creation_worker_lease_ttl_seconds",
    label: "Worker 租约 TTL（秒）",
    kind: "int",
    description: "执行租约有效期，超过后任务可被恢复流程回收。",
  },
  {
    key: "creation_worker_heartbeat_seconds",
    label: "Worker 心跳间隔（秒）",
    kind: "int",
    description: "运行中任务上报心跳的频率，越小恢复越快但写库更频繁。",
  },
  {
    key: "quota_enforce_concurrency_limit",
    label: "启用并发配额限制",
    kind: "bool",
    description: "开启后提交任务会检查并发上限；关闭则不限制并发配额。",
  },
  {
    key: "quota_free_monthly_chapter_limit",
    label: "普通用户月章节限额",
    kind: "int",
    description: "普通用户每月最多可生成章节数。",
  },
  {
    key: "quota_free_monthly_token_limit",
    label: "普通用户月 Token 限额",
    kind: "int",
    description: "普通用户每月最多可消耗 Token 总量。",
  },
  {
    key: "quota_admin_monthly_chapter_limit",
    label: "管理员月章节限额",
    kind: "int",
    description: "管理员账号每月最多可生成章节数。",
  },
  {
    key: "quota_admin_monthly_token_limit",
    label: "管理员月 Token 限额",
    kind: "int",
    description: "管理员账号每月最多可消耗 Token 总量。",
  },
  {
    key: "llm_output_max_schema_retries",
    label: "结构化解析重试次数",
    kind: "int",
    description: "单个 Provider 单种方法下，结构化校验失败后的重试次数。",
  },
  {
    key: "llm_output_max_provider_fallbacks",
    label: "Provider 最大回退次数",
    kind: "int",
    description: "默认 Provider 失败后，最多尝试额外 Provider 的数量。",
  },
  {
    key: "llm_output_min_chars",
    label: "正文最小字符数",
    kind: "int",
    description: "章节正文最小长度门槛，低于该值视为契约失败。",
  },
];

function defaultProvider(priority: number): AdminModelProvider {
  return {
    provider_key: `provider_${priority}`,
    display_name: `Provider ${priority}`,
    adapter_type: "openai_compatible",
    base_url: "",
    api_key: "",
    is_enabled: true,
    priority,
    models: [
      {
        model_name: "",
        model_type: "chat",
        is_default: true,
        is_enabled: true,
      },
    ],
  };
}

export default function AdminSettingsPage() {
  const router = useRouter();
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);

  const [loading, setLoading] = useState(true);
  const [savingModels, setSavingModels] = useState(false);
  const [savingRuntime, setSavingRuntime] = useState(false);
  const [error, setError] = useState("");

  const [securityMode, setSecurityMode] = useState<string>("plaintext");
  const [providers, setProviders] = useState<AdminModelProvider[]>([]);
  const [selectedProviderIndex, setSelectedProviderIndex] = useState<number | null>(0);
  const [runtimeItems, setRuntimeItems] = useState<AdminRuntimeSettingsResponse["items"]>([]);
  const [runtimeDraft, setRuntimeDraft] = useState<Record<string, unknown>>({});

  useEffect(() => {
    let disposed = false;
    (async () => {
      try {
        const me = await api.me();
        if (disposed) return;
        if (me.user.role !== "admin") {
          router.replace("/");
          return;
        }
        setAuthUser(me.user);
      } catch {
        if (!disposed) router.replace("/auth/login");
      } finally {
        if (!disposed) setAuthLoading(false);
      }
    })();
    return () => {
      disposed = true;
    };
  }, [router]);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      let models;
      try {
        models = await api.getAdminModelSettings({ includeSecrets: true });
      } catch {
        models = await api.getAdminModelSettings();
      }
      const runtime = await api.getAdminRuntimeSettings();
      const nextProviders = models.providers || [];
      setProviders(nextProviders);
      setSelectedProviderIndex((prev) => {
        if (!nextProviders.length) return null;
        if (prev === null || prev < 0 || prev >= nextProviders.length) return 0;
        return prev;
      });
      setSecurityMode(models.security_mode || "plaintext");
      setRuntimeItems(runtime.items || []);
      const draft: Record<string, unknown> = {};
      for (const item of runtime.items || []) {
        draft[item.key] = item.value;
      }
      setRuntimeDraft(draft);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!authUser) return;
    loadAll();
  }, [authUser, loadAll]);

  const setDefaultModel = (providerIndex: number, modelIndex: number, modelType: AdminModelType) => {
    setProviders((prev) =>
      prev.map((provider, pi) => ({
        ...provider,
        models: (provider.models || []).map((model, mi) => {
          if (model.model_type !== modelType) return model;
          if (pi === providerIndex && mi === modelIndex) {
            return { ...model, is_default: true };
          }
          return { ...model, is_default: false };
        }),
      }))
    );
  };

  const defaultsByType = useMemo(() => {
    const count: Record<string, number> = { chat: 0, embedding: 0, image: 0, video: 0 };
    providers.forEach((provider) => {
      provider.models?.forEach((m) => {
        if (m.is_default) count[m.model_type] = (count[m.model_type] || 0) + 1;
      });
    });
    return count;
  }, [providers]);

  const hasInvalidDefault = Object.values(defaultsByType).some((n) => n > 1);
  const selectedProvider =
    selectedProviderIndex !== null && selectedProviderIndex >= 0 && selectedProviderIndex < providers.length
      ? providers[selectedProviderIndex]
      : null;

  const updateProviderAt = useCallback((index: number, updater: (provider: AdminModelProvider) => AdminModelProvider) => {
    setProviders((prev) => prev.map((provider, i) => (i === index ? updater(provider) : provider)));
  }, []);

  const saveModels = async () => {
    setSavingModels(true);
    setError("");
    try {
      const payload = providers
        .map((p, idx) => {
          const providerKey = (p.provider_key || "").trim().toLowerCase();
          const displayName = (p.display_name || "").trim() || providerKey;
          const baseUrl = (p.base_url || "").trim();
          return {
            provider_key: providerKey,
            display_name: displayName,
            adapter_type: p.adapter_type,
            base_url: baseUrl || null,
            api_key: typeof p.api_key === "string" ? p.api_key : null,
            is_enabled: !!p.is_enabled,
            priority: Number.isFinite(p.priority) ? p.priority : (idx + 1) * 10,
            models: (p.models || [])
              .map((m) => ({
                model_name: (m.model_name || "").trim(),
                model_type: m.model_type,
                is_default: !!m.is_default,
                is_enabled: !!m.is_enabled,
                metadata: m.metadata || {},
              }))
              .filter((m) => m.model_name.length > 0),
          };
        })
        .filter((p) => p.provider_key.length > 0);
      await api.updateAdminModelSettings(payload);
      await loadAll();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSavingModels(false);
    }
  };

  const saveRuntime = async () => {
    setSavingRuntime(true);
    setError("");
    try {
      const updates: Record<string, unknown> = {};
      for (const field of RUNTIME_FIELDS) {
        const value = runtimeDraft[field.key];
        if (field.kind === "int") {
          if (value === "" || value === null || value === undefined) {
            updates[field.key] = null;
          } else {
            const normalized = Number(value);
            updates[field.key] = Number.isFinite(normalized) ? normalized : null;
          }
        } else if (field.kind === "enum") {
          const normalized = String(value ?? "").trim();
          updates[field.key] = normalized.length > 0 ? normalized : null;
        } else {
          updates[field.key] = Boolean(value);
        }
      }
      await api.updateAdminRuntimeSettings(updates);
      await loadAll();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSavingRuntime(false);
    }
  };

  const resetRuntimeToEnv = async (key: string) => {
    try {
      await api.updateAdminRuntimeSettings({ [key]: null });
      await loadAll();
    } catch (err) {
      setError(getErrorMessage(err));
    }
  };

  const removeProvider = (index: number) => {
    const total = providers.length;
    setProviders((prev) => prev.filter((_, i) => i !== index));
    setSelectedProviderIndex((prev) => {
      if (total <= 1) return null;
      if (prev === null) return null;
      if (prev === index) {
        return index >= total - 1 ? total - 2 : index;
      }
      if (prev > index) return prev - 1;
      return prev;
    });
  };

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
      <div className="max-w-[1400px] mx-auto px-4 py-8 space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-[#C8211B]/10 flex items-center justify-center">
              <Settings2 className="w-5 h-5 text-[#C8211B]" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-[#1F1B18]">系统设置</h1>
              <p className="text-sm text-[#8B8379]">模型 Provider、默认模型、调度与配额配置（页面配置优先于环境变量）</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => loadAll()}
            disabled={loading}
            className="h-9 px-3 text-sm rounded-[10px] border border-[#DDD8D3] text-[#3E3833] hover:bg-[#F2EEEA] inline-flex items-center gap-1.5 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            刷新
          </button>
        </div>

        {securityMode === "plaintext" ? (
          <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-amber-800 text-sm flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 mt-0.5" />
            <span>当前未配置 `SYSTEM_SETTINGS_MASTER_KEY`，API Key 以明文方式存储。建议尽快配置主密钥后再保存密钥。</span>
          </div>
        ) : null}

        {error ? (
          <div className="rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-red-700 text-sm">{error}</div>
        ) : null}

        <section className="rounded-2xl border border-[#E6DED6] bg-white p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-[#1F1B18]">模型配置</h2>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  const nextIndex = providers.length;
                  setProviders((prev) => [...prev, defaultProvider((prev.length + 1) * 10)]);
                  setSelectedProviderIndex(nextIndex);
                }}
              >
                <Plus className="w-4 h-4 mr-1" />新增 Provider
              </Button>
              <Button type="button" size="sm" onClick={saveModels} loading={savingModels} disabled={hasInvalidDefault}>
                <Save className="w-4 h-4 mr-1" />保存模型配置
              </Button>
            </div>
          </div>

          {hasInvalidDefault ? (
            <p className="text-sm text-red-600">同一模型类型只能有一个默认模型，请检查“设为默认”勾选。</p>
          ) : null}

          <div className="grid grid-cols-1 xl:grid-cols-[320px_minmax(0,1fr)] gap-4 xl:h-[760px] xl:max-h-[760px]">
            <aside className="rounded-xl border border-[#E6DED6] bg-[#FCFBFA] p-2 space-y-2 xl:h-full xl:overflow-y-auto">
              {providers.length ? (
                providers.map((provider, pIdx) => {
                  const chatDefault = (provider.models || []).find((m) => m.model_type === "chat" && m.is_default)?.model_name;
                  const isActive = selectedProviderIndex === pIdx;
                  return (
                    <div
                      key={`${provider.provider_key}-${pIdx}`}
                      className={`rounded-lg border p-2 transition-colors ${
                        isActive ? "border-[#C8211B] bg-[#FFF6F5]" : "border-[#E6DED6] bg-white"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => setSelectedProviderIndex(pIdx)}
                        className="w-full text-left"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="text-sm font-semibold text-[#1F1B18] truncate">
                              {provider.display_name || provider.provider_key || `Provider ${pIdx + 1}`}
                            </div>
                            <div className="mt-0.5 text-xs text-[#8B8379] truncate">
                              {provider.provider_key || "未命名 provider"}
                            </div>
                          </div>
                          <span
                            className={`shrink-0 text-[11px] px-2 py-0.5 rounded-full ${
                              provider.is_enabled ? "bg-emerald-50 text-emerald-700" : "bg-[#F3F1EE] text-[#8B8379]"
                            }`}
                          >
                            {provider.is_enabled ? "启用" : "禁用"}
                          </span>
                        </div>
                        <div className="mt-2 text-[11px] text-[#8B8379] space-x-2">
                          <span>回退优先级 {provider.priority}</span>
                          <span>模型 {(provider.models || []).length}</span>
                          <span>默认 {chatDefault || "-"}</span>
                        </div>
                      </button>
                      <div className="mt-2 flex items-center gap-2">
                        <button
                          type="button"
                          className={`h-7 px-2 rounded-[8px] text-xs border ${
                            provider.is_enabled
                              ? "border-emerald-300 text-emerald-700 bg-emerald-50"
                              : "border-[#DDD8D3] text-[#6B635D]"
                          }`}
                          onClick={() => updateProviderAt(pIdx, (current) => ({ ...current, is_enabled: !current.is_enabled }))}
                        >
                          {provider.is_enabled ? "停用" : "启用"}
                        </button>
                        <button
                          type="button"
                          className="h-7 px-2 rounded-[8px] text-xs border border-red-200 text-red-600 hover:bg-red-50"
                          onClick={() => removeProvider(pIdx)}
                        >
                          删除
                        </button>
                      </div>
                    </div>
                  );
                })
              ) : (
                <div className="rounded-lg border border-dashed border-[#DDD8D3] p-4 text-xs text-[#8B8379] text-center">
                  暂无 Provider，点击右上角“新增 Provider”创建
                </div>
              )}
            </aside>

            <div className="rounded-xl border border-[#E6DED6] p-4 xl:h-full xl:overflow-y-auto">
              {selectedProvider && selectedProviderIndex !== null ? (
                <div className="space-y-4">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <h3 className="text-sm font-semibold text-[#1F1B18]">Provider 详情</h3>
                      <p className="text-xs text-[#8B8379] mt-1">
                        配置来源：{selectedProvider.source === "db" ? "页面配置" : "环境变量"} ·
                        回退优先级：{selectedProvider.priority}（仅默认 Provider 调用失败时生效）
                      </p>
                    </div>
                    <span
                      className={`text-xs px-2 py-0.5 rounded-full ${
                        selectedProvider.is_enabled ? "bg-emerald-50 text-emerald-700" : "bg-[#F3F1EE] text-[#8B8379]"
                      }`}
                    >
                      {selectedProvider.is_enabled ? "已启用" : "已禁用"}
                    </span>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs text-[#8B8379] mb-1">Provider Key</label>
                      <input
                        value={selectedProvider.provider_key}
                        onChange={(e) => updateProviderAt(selectedProviderIndex, (current) => ({ ...current, provider_key: e.target.value }))}
                        className="w-full h-9 px-3 rounded-[10px] border border-[#DDD8D3] text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-[#8B8379] mb-1">展示名称</label>
                      <input
                        value={selectedProvider.display_name}
                        onChange={(e) => updateProviderAt(selectedProviderIndex, (current) => ({ ...current, display_name: e.target.value }))}
                        className="w-full h-9 px-3 rounded-[10px] border border-[#DDD8D3] text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-[#8B8379] mb-1">适配器</label>
                      <Select
                        value={selectedProvider.adapter_type}
                        onValueChange={(v) => updateProviderAt(selectedProviderIndex, (current) => ({ ...current, adapter_type: v as AdminAdapterType }))}
                        className="h-9 px-3 py-2 text-sm"
                        options={ADAPTER_OPTIONS}
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-[#8B8379] mb-1">回退优先级（越小越先尝试）</label>
                      <input
                        type="number"
                        value={selectedProvider.priority}
                        onChange={(e) =>
                          updateProviderAt(selectedProviderIndex, (current) => ({
                            ...current,
                            priority: parseInt(e.target.value || "0", 10) || 0,
                          }))
                        }
                        className="w-full h-9 px-3 rounded-[10px] border border-[#DDD8D3] text-sm"
                      />
                      <p className="mt-1 text-[11px] text-[#8B8379]">主流程优先走默认模型；仅在调用失败时按此顺序回退。</p>
                    </div>
                    <div className="md:col-span-2">
                      <label className="block text-xs text-[#8B8379] mb-1">Base URL</label>
                      <input
                        value={selectedProvider.base_url || ""}
                        onChange={(e) => updateProviderAt(selectedProviderIndex, (current) => ({ ...current, base_url: e.target.value }))}
                        placeholder="https://api.example.com/v1"
                        className="w-full h-9 px-3 rounded-[10px] border border-[#DDD8D3] text-sm"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs text-[#8B8379] mb-1">API Key（留空不改，输入空字符串将清空）</label>
                    <input
                      value={selectedProvider.api_key ?? selectedProvider.api_key_value ?? ""}
                      onChange={(e) => updateProviderAt(selectedProviderIndex, (current) => ({ ...current, api_key: e.target.value }))}
                      placeholder={selectedProvider.api_key_masked || "sk-..."}
                      className="w-full h-9 px-3 rounded-[10px] border border-[#DDD8D3] text-sm"
                    />
                    <p className="mt-1 text-xs text-[#8B8379]">
                      密钥来源：{selectedProvider.api_key_source || "none"} · 存储方式：
                      {selectedProvider.api_key_is_encrypted ? "加密" : "明文/环境变量"}
                    </p>
                  </div>

                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-medium text-[#3E3833]">模型列表</h3>
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() =>
                          updateProviderAt(selectedProviderIndex, (current) => ({
                            ...current,
                            models: [
                              ...(current.models || []),
                              {
                                model_name: "",
                                model_type: "chat",
                                is_default: false,
                                is_enabled: true,
                              },
                            ],
                          }))
                        }
                      >
                        <Plus className="w-3.5 h-3.5 mr-1" />新增模型
                      </Button>
                    </div>

                    {(selectedProvider.models || []).map((model, mIdx) => (
                      <div key={`${model.model_name}-${mIdx}`} className="grid grid-cols-1 md:grid-cols-7 gap-2 items-end">
                        <div className="md:col-span-3">
                          <label className="block text-xs text-[#8B8379] mb-1">模型名</label>
                          <input
                            value={model.model_name}
                            onChange={(e) =>
                              updateProviderAt(selectedProviderIndex, (current) => ({
                                ...current,
                                models: (current.models || []).map((item, idx) =>
                                  idx === mIdx ? { ...item, model_name: e.target.value } : item
                                ),
                              }))
                            }
                            className="w-full h-9 px-3 rounded-[10px] border border-[#DDD8D3] text-sm"
                          />
                        </div>
                        <div className="md:col-span-2">
                          <label className="block text-xs text-[#8B8379] mb-1">模型类型</label>
                          <Select
                            value={model.model_type}
                            onValueChange={(v) =>
                              updateProviderAt(selectedProviderIndex, (current) => ({
                                ...current,
                                models: (current.models || []).map((item, idx) =>
                                  idx === mIdx ? { ...item, model_type: v as AdminModelType, is_default: false } : item
                                ),
                              }))
                            }
                            className="h-9 px-3 py-2 text-sm"
                            options={MODEL_TYPE_OPTIONS}
                          />
                        </div>
                        <div className="md:col-span-1">
                          <label className="block text-xs text-[#8B8379] mb-1">启用</label>
                          <input
                            type="checkbox"
                            checked={!!model.is_enabled}
                            onChange={(e) =>
                              updateProviderAt(selectedProviderIndex, (current) => ({
                                ...current,
                                models: (current.models || []).map((item, idx) =>
                                  idx === mIdx ? { ...item, is_enabled: e.target.checked } : item
                                ),
                              }))
                            }
                            className="h-4 w-4"
                          />
                        </div>
                        <div className="md:col-span-1 flex items-center gap-2">
                          <label className="text-xs text-[#8B8379] inline-flex items-center gap-1">
                            <input
                              type="checkbox"
                              checked={!!model.is_default}
                              onChange={(e) => {
                                if (e.target.checked) {
                                  setDefaultModel(selectedProviderIndex, mIdx, model.model_type);
                                } else {
                                  updateProviderAt(selectedProviderIndex, (current) => ({
                                    ...current,
                                    models: (current.models || []).map((item, idx) =>
                                      idx === mIdx ? { ...item, is_default: false } : item
                                    ),
                                  }));
                                }
                              }}
                              className="h-4 w-4"
                            />
                            默认
                          </label>
                          <button
                            type="button"
                            className="h-8 px-2 rounded-lg border border-red-200 text-red-600 hover:bg-red-50"
                            onClick={() =>
                              updateProviderAt(selectedProviderIndex, (current) => ({
                                ...current,
                                models: (current.models || []).filter((_, idx) => idx !== mIdx),
                              }))
                            }
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="h-full min-h-[320px] rounded-lg border border-dashed border-[#DDD8D3] bg-[#FCFBFA] grid place-items-center">
                  <div className="text-center px-6">
                    <div className="text-sm font-medium text-[#3E3833]">请选择左侧 Provider</div>
                    <p className="text-xs text-[#8B8379] mt-1">选中后可编辑基础配置、API Key 与模型列表。</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>

        <section className="rounded-2xl border border-[#E6DED6] bg-white p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-[#1F1B18]">调度与配额配置</h2>
            <Button type="button" size="sm" onClick={saveRuntime} loading={savingRuntime}>
              <Save className="w-4 h-4 mr-1" />保存运行时配置
            </Button>
          </div>

          <p className="text-xs text-[#8B8379]">
            下列配置会即时影响调度与配额判断。标记为“页面配置”表示已覆盖环境变量；点击“恢复为环境变量”可回退。
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {RUNTIME_FIELDS.map((field) => {
              const item = runtimeItems.find((x) => x.key === field.key);
              const currentValue = runtimeDraft[field.key];
              return (
                <div key={field.key} className="rounded-xl border border-[#E6DED6] p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <label className="text-sm font-medium text-[#3E3833]">{field.label}</label>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${item?.source === "db" ? "bg-[#F8ECEA] text-[#A52A25]" : "bg-[#F3F1EE] text-[#8B8379]"}`}>
                      {item?.source === "db" ? "页面配置" : "环境变量"}
                    </span>
                  </div>
                  <p className="text-xs text-[#8B8379]">{field.description}</p>

                  {field.kind === "bool" ? (
                    <label className="inline-flex items-center gap-2 text-sm text-[#3E3833]">
                      <input
                        type="checkbox"
                        checked={Boolean(currentValue)}
                        onChange={(e) => setRuntimeDraft((prev) => ({ ...prev, [field.key]: e.target.checked }))}
                        className="h-4 w-4"
                      />
                      启用
                    </label>
                  ) : field.kind === "enum" ? (
                    <Select
                      value={String(currentValue ?? field.options?.[0]?.value ?? "")}
                      onValueChange={(v) => setRuntimeDraft((prev) => ({ ...prev, [field.key]: v }))}
                      className="h-9 px-3 py-2 text-sm"
                      options={field.options || []}
                    />
                  ) : (
                    <input
                      type="number"
                      value={typeof currentValue === "number" ? String(currentValue) : String(currentValue ?? "")}
                      onChange={(e) => setRuntimeDraft((prev) => ({ ...prev, [field.key]: e.target.value }))}
                      className="w-full h-9 px-3 rounded-[10px] border border-[#DDD8D3] text-sm"
                    />
                  )}

                  <button
                    type="button"
                    className="text-xs text-[#8B8379] hover:text-[#3E3833]"
                    onClick={() => resetRuntimeToEnv(field.key)}
                  >
                    恢复为环境变量
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </main>
  );
}
