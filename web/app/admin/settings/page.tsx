"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, RefreshCw, Save, Settings2 } from "lucide-react";

import {
  AdminEmbeddingSettings,
  AdminPrimaryChatSettings,
  AdminProtocolOverride,
  AdminRuntimeSettingsResponse,
  AuthUser,
  api,
  getErrorMessage,
} from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";

const PROVIDER_OPTIONS = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
];

const PROTOCOL_OPTIONS: Array<{ value: "" | AdminProtocolOverride; label: string }> = [
  { value: "", label: "自动推断" },
  { value: "openai_compatible", label: "OpenAI 兼容" },
  { value: "anthropic", label: "Anthropic 原生" },
  { value: "gemini", label: "Gemini 原生" },
];

type RuntimeFieldKind = "bool" | "int";
type RuntimeField = {
  key: string;
  label: string;
  kind: RuntimeFieldKind;
  description: string;
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
    description: "每个用户可同时执行的默认任务数。",
  },
];

const EMPTY_PRIMARY_CHAT: AdminPrimaryChatSettings = {
  provider: "openai",
  model: "",
  base_url: "",
  api_key: "",
  protocol_override: null,
  resolved_protocol: "openai_compatible",
  api_key_masked: "",
  api_key_source: "none",
  api_key_is_encrypted: false,
  source: "env",
};

const EMPTY_EMBEDDING: AdminEmbeddingSettings = {
  enabled: true,
  model: "",
  reuse_primary_connection: true,
  base_url: "",
  api_key: "",
  protocol_override: null,
  resolved_protocol: "openai_compatible",
  api_key_masked: "",
  api_key_source: "none",
  api_key_is_encrypted: false,
  source: "env",
};

function sourceLabel(source?: string) {
  return source === "db" ? "页面配置" : "环境变量";
}

function securityLabel(isEncrypted?: boolean, source?: string) {
  if (source === "env") return "明文/环境变量";
  return isEncrypted ? "加密存储" : "明文存储";
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
  const [primaryChat, setPrimaryChat] = useState<AdminPrimaryChatSettings>(EMPTY_PRIMARY_CHAT);
  const [embedding, setEmbedding] = useState<AdminEmbeddingSettings>(EMPTY_EMBEDDING);
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
      const models = await api.getAdminModelSettings({ includeSecrets: true });
      const runtime = await api.getAdminRuntimeSettings();
      setPrimaryChat({
        ...EMPTY_PRIMARY_CHAT,
        ...models.primary_chat,
        api_key: models.primary_chat?.api_key_value ?? "",
      });
      setEmbedding({
        ...EMPTY_EMBEDDING,
        ...models.embedding,
        api_key: models.embedding?.api_key_value ?? "",
      });
      setSecurityMode(models.security_mode || "plaintext");
      setRuntimeItems(runtime.items || []);
      const draft: Record<string, unknown> = {};
      for (const item of runtime.items || []) draft[item.key] = item.value;
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

  const saveModels = async () => {
    setSavingModels(true);
    setError("");
    try {
      await api.updateAdminModelSettings({
        primary_chat: {
          provider: primaryChat.provider,
          model: (primaryChat.model || "").trim(),
          base_url: (primaryChat.base_url || "").trim() || null,
          api_key: primaryChat.api_key ?? "",
          protocol_override: primaryChat.protocol_override || null,
        },
        embedding: {
          enabled: !!embedding.enabled,
          model: (embedding.model || "").trim() || null,
          reuse_primary_connection: !!embedding.reuse_primary_connection,
          base_url: embedding.reuse_primary_connection ? null : (embedding.base_url || "").trim() || null,
          api_key: embedding.reuse_primary_connection ? "" : (embedding.api_key ?? ""),
          protocol_override: embedding.reuse_primary_connection ? null : embedding.protocol_override || null,
        },
      });
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
        } else {
          const boolValue = Boolean(value);
          const loaded = runtimeItems.find((x) => x.key === field.key);
          if (loaded && loaded.source === "env" && Boolean(loaded.value) === boolValue) {
            updates[field.key] = null;
          } else {
            updates[field.key] = boolValue;
          }
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

  const primaryProtocolDescription = useMemo(() => {
    if (primaryChat.protocol_override) return `协议覆盖：${primaryChat.protocol_override}`;
    if (primaryChat.base_url) return "检测到自定义 Base URL，默认按 OpenAI 兼容协议处理。";
    if (primaryChat.provider === "gemini") return "未配置 Base URL，当前走 Gemini 原生协议。";
    if (primaryChat.provider === "anthropic") return "未配置 Base URL，当前走 Anthropic 原生协议。";
    return "未配置 Base URL，当前走 OpenAI 兼容协议。";
  }, [primaryChat.base_url, primaryChat.provider, primaryChat.protocol_override]);

  const embeddingProtocolDescription = useMemo(() => {
    if (!embedding.enabled) return "当前未启用向量模型。";
    if (embedding.reuse_primary_connection) return "复用主模型连接；仅当主模型为 OpenAI 兼容协议时可用。";
    if (embedding.base_url) return "使用独立向量网关，默认按 OpenAI 兼容协议处理。";
    return "未配置 Base URL 时，独立向量模型将使用 OpenAI 官方兼容端点。";
  }, [embedding.base_url, embedding.enabled, embedding.reuse_primary_connection]);

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
      <div className="mx-auto max-w-[1280px] space-y-6 px-4 py-8">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#C8211B]/10">
              <Settings2 className="h-5 w-5 text-[#C8211B]" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-[#1F1B18]">系统设置</h1>
              <p className="text-sm text-[#8B8379]">只保留一个主模型和一个向量模型，页面配置优先于环境变量。</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => loadAll()}
            disabled={loading}
            className="inline-flex h-9 items-center gap-1.5 rounded-[10px] border border-[#DDD8D3] px-3 text-sm text-[#3E3833] hover:bg-[#F2EEEA] disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            刷新
          </button>
        </div>

        {securityMode === "plaintext" ? (
          <div className="flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <AlertTriangle className="mt-0.5 h-4 w-4" />
            <span>当前未配置 `SYSTEM_SETTINGS_MASTER_KEY`，页面保存的 API Key 将以明文方式存储。</span>
          </div>
        ) : null}

        {error ? <div className="rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div> : null}

        <section className="space-y-5 rounded-2xl border border-[#E6DED6] bg-white p-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="text-base font-semibold text-[#1F1B18]">模型配置</h2>
              <p className="mt-1 text-sm text-[#8B8379]">主模型负责文本生成；向量模型负责检索和上下文召回。</p>
            </div>
            <Button type="button" size="sm" onClick={saveModels} loading={savingModels}>
              <Save className="mr-1 h-4 w-4" />
              保存模型配置
            </Button>
          </div>

          <div className="grid gap-5 xl:grid-cols-2">
            <section className="space-y-4 rounded-2xl border border-[#E6DED6] bg-[#FCFBFA] p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-lg font-semibold text-[#1F1B18]">主模型</h3>
                  <p className="mt-1 text-sm text-[#8B8379]">来源：{sourceLabel(primaryChat.source)}</p>
                </div>
                <span className="rounded-full bg-[#F6F2EE] px-3 py-1 text-xs text-[#8B8379]">{primaryChat.resolved_protocol || "自动推断"}</span>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <Select
                  label="模型提供方"
                  value={primaryChat.provider}
                  options={PROVIDER_OPTIONS}
                  onValueChange={(value) => setPrimaryChat((prev) => ({ ...prev, provider: value as AdminPrimaryChatSettings["provider"] }))}
                />
                <label className="space-y-2 text-sm font-medium text-[#3A3A3C]">
                  <span>模型名称</span>
                  <input
                    className="h-12 w-full rounded-[10px] border border-[#DDD8D3] bg-white px-4 text-[#1F1B18] outline-none focus:border-[#C8211B]"
                    value={primaryChat.model || ""}
                    onChange={(e) => setPrimaryChat((prev) => ({ ...prev, model: e.target.value }))}
                    placeholder="例如：gemini-2.5-pro"
                  />
                </label>
              </div>

              <label className="space-y-2 text-sm font-medium text-[#3A3A3C]">
                <span>Base URL</span>
                <input
                  className="h-12 w-full rounded-[10px] border border-[#DDD8D3] bg-white px-4 text-[#1F1B18] outline-none focus:border-[#C8211B]"
                  value={primaryChat.base_url || ""}
                  onChange={(e) => setPrimaryChat((prev) => ({ ...prev, base_url: e.target.value }))}
                  placeholder="留空时走官方默认端点"
                />
              </label>

              <label className="space-y-2 text-sm font-medium text-[#3A3A3C]">
                <span>API Key</span>
                <input
                  className="h-12 w-full rounded-[10px] border border-[#DDD8D3] bg-white px-4 text-[#1F1B18] outline-none focus:border-[#C8211B]"
                  value={primaryChat.api_key ?? ""}
                  onChange={(e) => setPrimaryChat((prev) => ({ ...prev, api_key: e.target.value }))}
                  placeholder="留空不改，输入空字符串将清空"
                />
              </label>

              <div className="rounded-xl bg-white px-4 py-3 text-sm text-[#8B8379]">
                <p>{primaryProtocolDescription}</p>
                <p className="mt-1">密钥来源：{sourceLabel(primaryChat.source)} · 存储方式：{securityLabel(primaryChat.api_key_is_encrypted, primaryChat.source)}</p>
              </div>

              <details className="rounded-xl border border-[#E6DED6] bg-white px-4 py-3">
                <summary className="cursor-pointer text-sm font-medium text-[#3A3A3C]">高级协议覆盖</summary>
                <div className="mt-3">
                  <Select
                    value={(primaryChat.protocol_override || "") as "" | AdminProtocolOverride}
                    options={PROTOCOL_OPTIONS}
                    onValueChange={(value) => setPrimaryChat((prev) => ({ ...prev, protocol_override: (value || null) as AdminProtocolOverride | null }))}
                  />
                </div>
              </details>
            </section>

            <section className="space-y-4 rounded-2xl border border-[#E6DED6] bg-[#FCFBFA] p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h3 className="text-lg font-semibold text-[#1F1B18]">向量模型</h3>
                  <p className="mt-1 text-sm text-[#8B8379]">来源：{sourceLabel(embedding.source)}</p>
                </div>
                <label className="inline-flex items-center gap-2 text-sm text-[#3A3A3C]">
                  <input
                    type="checkbox"
                    checked={!!embedding.enabled}
                    onChange={(e) => setEmbedding((prev) => ({ ...prev, enabled: e.target.checked }))}
                  />
                  启用
                </label>
              </div>

              <label className="inline-flex items-center gap-2 text-sm text-[#3A3A3C]">
                <input
                  type="checkbox"
                  checked={!!embedding.reuse_primary_connection}
                  onChange={(e) => setEmbedding((prev) => ({ ...prev, reuse_primary_connection: e.target.checked }))}
                />
                复用主模型连接
              </label>

              <label className="space-y-2 text-sm font-medium text-[#3A3A3C]">
                <span>向量模型名称</span>
                <input
                  className="h-12 w-full rounded-[10px] border border-[#DDD8D3] bg-white px-4 text-[#1F1B18] outline-none focus:border-[#C8211B]"
                  value={embedding.model || ""}
                  onChange={(e) => setEmbedding((prev) => ({ ...prev, model: e.target.value }))}
                  placeholder="例如：text-embedding-3-small"
                />
              </label>

              {!embedding.reuse_primary_connection ? (
                <>
                  <label className="space-y-2 text-sm font-medium text-[#3A3A3C]">
                    <span>独立 Base URL</span>
                    <input
                      className="h-12 w-full rounded-[10px] border border-[#DDD8D3] bg-white px-4 text-[#1F1B18] outline-none focus:border-[#C8211B]"
                      value={embedding.base_url || ""}
                      onChange={(e) => setEmbedding((prev) => ({ ...prev, base_url: e.target.value }))}
                      placeholder="留空时走 OpenAI 官方兼容端点"
                    />
                  </label>

                  <label className="space-y-2 text-sm font-medium text-[#3A3A3C]">
                    <span>独立 API Key</span>
                    <input
                      className="h-12 w-full rounded-[10px] border border-[#DDD8D3] bg-white px-4 text-[#1F1B18] outline-none focus:border-[#C8211B]"
                      value={embedding.api_key ?? ""}
                      onChange={(e) => setEmbedding((prev) => ({ ...prev, api_key: e.target.value }))}
                      placeholder="留空不改，输入空字符串将清空"
                    />
                  </label>

                  <details className="rounded-xl border border-[#E6DED6] bg-white px-4 py-3">
                    <summary className="cursor-pointer text-sm font-medium text-[#3A3A3C]">高级协议覆盖</summary>
                    <div className="mt-3">
                      <Select
                        value={(embedding.protocol_override || "") as "" | AdminProtocolOverride}
                        options={PROTOCOL_OPTIONS.filter((item) => item.value !== "anthropic" && item.value !== "gemini")}
                        onValueChange={(value) => setEmbedding((prev) => ({ ...prev, protocol_override: (value || null) as AdminProtocolOverride | null }))}
                      />
                    </div>
                  </details>
                </>
              ) : null}

              <div className="rounded-xl bg-white px-4 py-3 text-sm text-[#8B8379]">
                <p>{embeddingProtocolDescription}</p>
                <p className="mt-1">密钥来源：{sourceLabel(embedding.source)} · 存储方式：{securityLabel(embedding.api_key_is_encrypted, embedding.source)}</p>
              </div>
            </section>
          </div>
        </section>

        <section className="space-y-4 rounded-2xl border border-[#E6DED6] bg-white p-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="text-base font-semibold text-[#1F1B18]">调度与配额配置</h2>
              <p className="mt-1 text-sm text-[#8B8379]">这些配置仍保持“页面配置优先，未覆盖时回退环境变量”。</p>
            </div>
            <Button type="button" size="sm" onClick={saveRuntime} loading={savingRuntime}>
              <Save className="mr-1 h-4 w-4" />
              保存运行时配置
            </Button>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            {RUNTIME_FIELDS.map((field) => {
              const item = runtimeItems.find((x) => x.key === field.key);
              const source = item?.source || "env";
              const draftValue = runtimeDraft[field.key];
              return (
                <div key={field.key} className="space-y-3 rounded-2xl border border-[#E6DED6] bg-[#FCFBFA] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold text-[#1F1B18]">{field.label}</h3>
                      <p className="mt-1 text-sm text-[#8B8379]">{field.description}</p>
                    </div>
                    <span className="rounded-full bg-[#F6F2EE] px-3 py-1 text-xs text-[#8B8379]">{sourceLabel(source)}</span>
                  </div>

                  {field.kind === "bool" ? (
                    <label className="inline-flex items-center gap-2 text-sm text-[#3A3A3C]">
                      <input
                        type="checkbox"
                        checked={Boolean(draftValue)}
                        onChange={(e) => setRuntimeDraft((prev) => ({ ...prev, [field.key]: e.target.checked }))}
                      />
                      启用
                    </label>
                  ) : (
                    <input
                      className="h-12 w-full rounded-[10px] border border-[#DDD8D3] bg-white px-4 text-[#1F1B18] outline-none focus:border-[#C8211B]"
                      type="number"
                      value={draftValue === null || draftValue === undefined ? "" : String(draftValue)}
                      onChange={(e) => setRuntimeDraft((prev) => ({ ...prev, [field.key]: e.target.value }))}
                    />
                  )}

                  <button
                    type="button"
                    onClick={() => resetRuntimeToEnv(field.key)}
                    className="text-sm text-[#8B8379] hover:text-[#1F1B18]"
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
