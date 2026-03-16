"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft, Gauge, Play, Pause, RotateCcw, Ban, Download, CheckCircle2, Copy } from "lucide-react";
import {
  api,
  StoryboardCharacterCard,
  StoryboardRun,
  StoryboardShot,
  StoryboardTaskStatus,
  StoryboardVersion,
  getErrorMessage,
} from "@/lib/api";
import { formatRunState, formatStoryboardLane, formatStoryboardPhase } from "@/lib/display";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ConfirmModal } from "@/components/ui";
import { Select } from "@/components/ui/Select";
import { TopBar } from "@/components/ui/TopBar";

export default function StoryboardWorkbenchPage() {
  const params = useParams();
  const search = useSearchParams();
  const projectId = Number(params.id);
  const taskIdFromUrl = search.get("task_id") || undefined;
  const runIdFromUrl = search.get("run_id") || undefined;

  const [status, setStatus] = useState<StoryboardTaskStatus | null>(null);
  const [currentRunId, setCurrentRunId] = useState<string | null>(runIdFromUrl || null);
  const [versions, setVersions] = useState<StoryboardVersion[]>([]);
  const [activeVersionId, setActiveVersionId] = useState<number | null>(null);
  const [shots, setShots] = useState<StoryboardShot[]>([]);
  const [characterCards, setCharacterCards] = useState<StoryboardCharacterCard[]>([]);
  const [episodeNo, setEpisodeNo] = useState<number | undefined>(undefined);
  const [episodeOptions, setEpisodeOptions] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [savingShotId, setSavingShotId] = useState<number | null>(null);
  const [savingCardId, setSavingCardId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<"shots" | "characters">("shots");
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string>("");
  const [actionMessageType, setActionMessageType] = useState<"success" | "error">("success");
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [showFinalizeConfirm, setShowFinalizeConfirm] = useState(false);
  const lastRunStateRef = useRef<string>("");
  const runEventSourceRef = useRef<EventSource | null>(null);

  const loadVersions = async () => {
    const vs = await api.listStoryboardVersions(projectId);
    setVersions(vs);
    const def = vs.find((v) => v.is_default) || vs[0];
    if (def && activeVersionId === null) {
      setActiveVersionId(def.id);
    }
    return vs;
  };

  const loadShots = async (versionId: number, ep?: number) => {
    const rows = await api.listStoryboardShots(projectId, versionId, ep);
    setShots(rows);
  };

  const loadEpisodeOptions = async (versionId: number) => {
    const rows = await api.listStoryboardShots(projectId, versionId);
    const set = new Set<number>();
    rows.forEach((s) => set.add(s.episode_no));
    setEpisodeOptions(Array.from(set).sort((a, b) => a - b));
  };

  const loadCharacterCards = async (versionId: number) => {
    const rows = await api.listStoryboardCharacterCards(projectId, versionId);
    setCharacterCards(rows);
  };

  const toLegacyStatus = (run: StoryboardRun, preferredLane?: string): StoryboardTaskStatus => {
    const lane =
      run.lanes.find((item) => item.lane === preferredLane) ||
      run.lanes.find((item) => item.status === "running" || item.run_state === "running") ||
      run.lanes[0];
    const gate = (lane?.gate_report_json || {}) as Record<string, unknown>;
    return {
      storyboard_project_id: run.storyboard_project_id,
      task_id: lane?.creation_task_public_id || undefined,
      status: run.status,
      run_state: run.run_state,
      current_phase: lane?.current_phase || run.current_phase || undefined,
      current_lane: lane?.lane,
      progress: Number(lane?.progress ?? run.progress ?? 0),
      message: lane?.message || run.message || undefined,
      error: lane?.error || run.error || undefined,
      error_code: lane?.error_code || run.error_code || undefined,
      error_category: lane?.error_category || run.error_category || undefined,
      retryable: undefined,
      style_consistency_score:
        typeof gate.style_consistency_score === "number" ? Number(gate.style_consistency_score) : undefined,
      hook_score_episode:
        gate.hook_score_episode && typeof gate.hook_score_episode === "object"
          ? (gate.hook_score_episode as Record<string, number>)
          : undefined,
      quality_gate_reasons: Array.isArray(gate.quality_gate_reasons)
        ? (gate.quality_gate_reasons as string[])
        : undefined,
      character_prompt_phase:
        typeof gate.character_prompt_phase === "string" ? String(gate.character_prompt_phase) : undefined,
      character_profiles_count:
        typeof gate.character_profiles_count === "number" ? Number(gate.character_profiles_count) : undefined,
      missing_identity_fields_count:
        typeof gate.missing_identity_fields_count === "number" ? Number(gate.missing_identity_fields_count) : undefined,
      failed_identity_characters: Array.isArray(gate.failed_identity_characters)
        ? (gate.failed_identity_characters as Array<Record<string, unknown>>)
        : undefined,
    };
  };

  const loadRuntimeStatus = async (
    runId: string | null,
    preferredLane?: string
  ): Promise<StoryboardTaskStatus | null> => {
    if (runId) {
      const run = await api.getStoryboardRun(projectId, runId);
      return toLegacyStatus(run, preferredLane);
    }
    return api.getStoryboardStatus(projectId, taskIdFromUrl).catch(() => null);
  };

  const refresh = async () => {
    setLoading(true);
    try {
      const vs = await loadVersions();
      const versionId = activeVersionId || vs.find((v) => v.is_default)?.id || vs[0]?.id;
      const preferredLane = versionId ? vs.find((v) => v.id === versionId)?.lane : undefined;
      const st = await loadRuntimeStatus(currentRunId, preferredLane);
      setStatus(st);
      if (versionId) {
        setActiveVersionId(versionId);
        await loadEpisodeOptions(versionId);
        await loadShots(versionId, episodeNo);
        await loadCharacterCards(versionId);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, [projectId]);

  useEffect(() => {
    if (runIdFromUrl) {
      setCurrentRunId(runIdFromUrl);
    }
  }, [runIdFromUrl]);

  useEffect(() => {
    if (!currentRunId) return;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (runEventSourceRef.current) {
        runEventSourceRef.current.close();
      }
      const stream = api.streamStoryboardRun(projectId, currentRunId);
      runEventSourceRef.current = stream;
      stream.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data || "{}") as { type?: string; payload?: StoryboardRun };
          if (data.type !== "run_status" || !data.payload) return;
          const preferredLane = activeVersionId ? versions.find((v) => v.id === activeVersionId)?.lane : undefined;
          setStatus(toLegacyStatus(data.payload, preferredLane));
          if (["completed", "failed", "cancelled"].includes(String(data.payload.run_state || ""))) {
            void refresh();
          }
        } catch {
          // ignore broken stream payload
        }
      };
      stream.onerror = () => {
        stream.close();
        if (reconnectTimer != null) {
          window.clearTimeout(reconnectTimer);
        }
        reconnectTimer = window.setTimeout(() => {
          connect();
        }, 2200);
      };
    };

    connect();
    return () => {
      if (reconnectTimer != null) {
        window.clearTimeout(reconnectTimer);
      }
      if (runEventSourceRef.current) {
        runEventSourceRef.current.close();
        runEventSourceRef.current = null;
      }
    };
  }, [projectId, currentRunId, activeVersionId, versions]);

  useEffect(() => {
    const timer = setInterval(() => {
      void (async () => {
        try {
          const preferredLane = activeVersionId ? versions.find((v) => v.id === activeVersionId)?.lane : undefined;
          const st = await loadRuntimeStatus(currentRunId, preferredLane);
          setStatus(st);
        } catch (e) {
          if (!(e instanceof Error) || !e.message.includes("404")) {
            // ignore polling errors
          }
        }
      })();
    }, 3000);
    return () => clearInterval(timer);
  }, [projectId, taskIdFromUrl, currentRunId, activeVersionId, versions]);

  useEffect(() => {
    if (activeVersionId) {
      void loadEpisodeOptions(activeVersionId);
      void loadShots(activeVersionId, episodeNo);
      void loadCharacterCards(activeVersionId);
    }
  }, [activeVersionId, episodeNo]);

  const activeVersion = versions.find((v) => v.id === activeVersionId) || null;
  const scoreCard = activeVersion?.quality_report_json || {};
  const missingIdentityCount = Number(status?.missing_identity_fields_count || scoreCard.missing_identity_fields_count || 0);
  const failedIdentityCharacters = (status?.failed_identity_characters || scoreCard.failed_identity_characters || []) as Array<Record<string, unknown>>;
  const isFinal = Boolean(activeVersion?.is_final);
  const canFinalize = Boolean(activeVersion && !isFinal && missingIdentityCount === 0 && characterCards.length > 0);
  const canExport = Boolean(activeVersion && isFinal && missingIdentityCount === 0 && characterCards.length > 0);
  const runState = status?.run_state || "";
  const canPause = ["running", "retrying", "submitted"].includes(runState);
  const canResume = ["paused"].includes(runState);
  const canCancel = ["running", "retrying", "submitted", "paused"].includes(runState);
  const canRetry = ["failed", "cancelled"].includes(runState);
  const canRegenerateCharacters = Boolean(activeVersion && !isFinal);
  const canOptimize = Boolean(activeVersion && !isFinal);
  const qualityGateReasons = (scoreCard.quality_gate_reasons || status?.quality_gate_reasons || []) as string[];
  const hasScoreData = Boolean(
    typeof scoreCard.style_consistency_score === "number" ||
    typeof scoreCard.shot_density_risk === "number" ||
    typeof scoreCard.completeness_rate === "number" ||
    Object.keys(scoreCard.hook_score_episode || {}).length > 0
  );
  const laneGroups = useMemo(() => {
    return {
      vertical: versions.filter((v) => v.lane === "vertical_feed"),
      horizontal: versions.filter((v) => v.lane === "horizontal_cinematic"),
    };
  }, [versions]);
  const toolbarBtnClass = "h-8 px-3 text-[13px]";

  const onUpdateShot = async (shotId: number, patch: Partial<StoryboardShot>) => {
    if (!activeVersionId || isFinal) return;
    setSavingShotId(shotId);
    try {
      await api.updateStoryboardShot(projectId, shotId, patch);
      await loadShots(activeVersionId, episodeNo);
    } catch (e) {
      const msg = getErrorMessage(e, "保存失败");
      setActionMessageType("error");
      setActionMessage(msg);
    } finally {
      setSavingShotId(null);
    }
  };

  const onUpdateCharacterCard = async (card: StoryboardCharacterCard) => {
    if (!activeVersionId || isFinal) return;
    setSavingCardId(card.id);
    try {
      await api.updateStoryboardCharacterCard(projectId, activeVersionId, card.id, {
        skin_tone: String(card.skin_tone || "").trim(),
        ethnicity: String(card.ethnicity || "").trim(),
        master_prompt_text: String(card.master_prompt_text || "").trim(),
        negative_prompt_text: String(card.negative_prompt_text || "").trim() || null,
        consistency_anchors_json: (card.consistency_anchors_json || []).map((x) => String(x).trim()).filter(Boolean),
      });
      await loadCharacterCards(activeVersionId);
      setActionMessageType("success");
      setActionMessage(`角色「${card.display_name || card.character_key}」已保存`);
    } catch (e) {
      const msg = getErrorMessage(e, "角色卡保存失败");
      setActionMessageType("error");
      setActionMessage(msg);
    } finally {
      setSavingCardId(null);
    }
  };

  const statusText = status?.message || (status?.run_state ? `状态：${formatRunState(status.run_state)}` : "暂无任务状态");

  const copyText = async (value: string, key: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey((k) => (k === key ? null : k)), 1300);
    } catch (_) {
      setCopiedKey("error");
      window.setTimeout(() => setCopiedKey(null), 1300);
    }
  };

  const runAction = async (key: string, fn: () => Promise<unknown>, success: string) => {
    setActionBusy(key);
    setActionMessage("");
    try {
      await fn();
      await refresh();
      setActionMessageType("success");
      setActionMessage(success);
    } catch (e) {
      const msg = getErrorMessage(e, "操作失败");
      setActionMessageType("error");
      setActionMessage(msg);
    } finally {
      setActionBusy(null);
    }
  };

  const requestExport = async (format: "csv" | "json" | "pdf") => {
    if (!activeVersion) return;
    const create = await api.createStoryboardExport(projectId, activeVersion.id, format, {
      idempotencyKey: `export-${projectId}-${activeVersion.id}-${format}-${Date.now()}`,
    });
    const wait = async () => {
      for (let i = 0; i < 30; i += 1) {
        const row = await api.getStoryboardExport(projectId, create.export_id);
        if (row.status === "completed" && row.download_url) {
          const url = api.resolveApiUrl(row.download_url);
          window.open(url, "_blank");
          return;
        }
        if (row.status === "failed") {
          throw new Error(row.error || "导出失败");
        }
        await new Promise((resolve) => window.setTimeout(resolve, 700));
      }
      throw new Error("导出任务仍在处理中，请稍后刷新查看");
    };
    await wait();
  };

  useEffect(() => {
    const currentState = status?.run_state;
    if (!currentState) return;
    const prevState = lastRunStateRef.current;
    if (prevState && prevState !== currentState && ["completed", "failed", "cancelled"].includes(currentState)) {
      void refresh();
    }
    lastRunStateRef.current = currentState;
  }, [status?.run_state]);

  return (
    <main className="min-h-screen">
      <TopBar
        title="导演分镜工作台"
        subtitle={statusText}
        backHref="/storyboards"
        icon={<ArrowLeft className="w-5 h-5" />}
        actions={
          <div className="flex flex-wrap items-center gap-2 justify-end max-w-[1040px]">
            <div className="flex items-center gap-1.5 rounded-xl border border-[#E5DED7] bg-[#FAF7F4] p-1">
              <Button variant="secondary" size="sm" className={toolbarBtnClass} loading={actionBusy === "refresh"} onClick={() => void runAction("refresh", async () => refresh(), "已刷新")}>
                刷新
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className={toolbarBtnClass}
                disabled={!canPause}
                loading={actionBusy === "pause"}
                onClick={() =>
                  void runAction(
                    "pause",
                    async () => {
                      if (currentRunId) {
                        await api.actionStoryboardRun(projectId, currentRunId, "pause");
                      } else {
                        await api.pauseStoryboard(projectId, status?.task_id);
                      }
                    },
                    "任务已暂停"
                  )
                }
              >
                <Pause className="w-4 h-4 mr-1.5" />暂停
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className={toolbarBtnClass}
                disabled={!canResume}
                loading={actionBusy === "resume"}
                onClick={() =>
                  void runAction(
                    "resume",
                    async () => {
                      if (currentRunId) {
                        await api.actionStoryboardRun(projectId, currentRunId, "resume");
                      } else {
                        await api.resumeStoryboard(projectId, status?.task_id);
                      }
                    },
                    "任务已恢复"
                  )
                }
              >
                <Play className="w-4 h-4 mr-1.5" />恢复
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className={toolbarBtnClass}
                disabled={!canRetry}
                loading={actionBusy === "retry"}
                onClick={() =>
                  void runAction(
                    "retry",
                    async () => {
                      if (currentRunId) {
                        const resp = await api.actionStoryboardRun(projectId, currentRunId, "retry", {
                          idempotencyKey: `retry-${projectId}-${Date.now()}`,
                        });
                        setCurrentRunId(resp.run_id);
                      } else {
                        await api.retryStoryboard(projectId);
                      }
                    },
                    "已提交重试任务"
                  )
                }
              >
                <RotateCcw className="w-4 h-4 mr-1.5" />重试
              </Button>
              <Button
                variant="secondary"
                size="sm"
                className={toolbarBtnClass}
                disabled={!canCancel}
                loading={actionBusy === "cancel"}
                onClick={() => setShowCancelConfirm(true)}
              >
                <Ban className="w-4 h-4 mr-1.5" />取消
              </Button>
            </div>

            <div className="flex items-center gap-1.5 rounded-xl border border-[#E5DED7] bg-[#FAF7F4] p-1">
              {activeVersion ? (
                <Button
                  variant="secondary"
                  size="sm"
                  className={toolbarBtnClass}
                  disabled={!canExport}
                  loading={actionBusy === "export-csv"}
                  onClick={() =>
                    void runAction(
                      "export-csv",
                      async () => {
                        await requestExport("csv");
                      },
                      "CSV 导出已完成"
                    )
                  }
                >
                  <Download className="w-4 h-4 mr-1.5" />导出CSV
                </Button>
              ) : null}
              {activeVersion ? (
                <Button
                  variant="secondary"
                  size="sm"
                  className={toolbarBtnClass}
                  disabled={!canExport}
                  loading={actionBusy === "export-json"}
                  onClick={() =>
                    void runAction(
                      "export-json",
                      async () => {
                        await requestExport("json");
                      },
                      "JSON 导出已完成"
                    )
                  }
                >
                  <Download className="w-4 h-4 mr-1.5" />导出JSON
                </Button>
              ) : null}
              {activeVersion ? (
                <Button
                  variant="secondary"
                  size="sm"
                  className={toolbarBtnClass}
                  disabled={!canExport}
                  loading={actionBusy === "export-pdf"}
                  onClick={() =>
                    void runAction(
                      "export-pdf",
                      async () => {
                        await requestExport("pdf");
                      },
                      "PDF 导出已完成"
                    )
                  }
                >
                  <Download className="w-4 h-4 mr-1.5" />导出PDF
                </Button>
              ) : null}
              {activeVersion ? (
                <Button
                  variant="secondary"
                  size="sm"
                  className={toolbarBtnClass}
                  disabled={!canRegenerateCharacters}
                  loading={actionBusy === "regen-char"}
                  onClick={() => void runAction("regen-char", async () => {
                    await api.regenerateStoryboardCharacterPrompts(projectId, activeVersion.id, activeVersion.lane);
                  }, "已生成人物主形象提示词")}
                >
                  生成人物主形象提示词
                </Button>
              ) : null}
              {activeVersion ? (
                <Button
                  variant="secondary"
                  size="sm"
                  className={toolbarBtnClass}
                  disabled={!canOptimize}
                  loading={actionBusy === "optimize"}
                  onClick={() => void runAction("optimize", async () => {
                    await api.optimizeStoryboardVersion(projectId, activeVersion.id);
                  }, "已应用优化建议")}
                >
                  一键优化到可拍
                </Button>
              ) : null}
              {activeVersion ? (
                <Button size="sm" className={toolbarBtnClass} disabled={!canFinalize} loading={actionBusy === "finalize"} onClick={() => setShowFinalizeConfirm(true)}>
                  <CheckCircle2 className="w-4 h-4 mr-1.5" />确认定稿
                </Button>
              ) : null}
            </div>
          </div>
        }
      />

      <div className="max-w-[1580px] mx-auto px-4 py-5 grid grid-cols-12 gap-4 lg:gap-5">
        {actionMessage ? (
          <div className={`col-span-12 rounded-xl border px-4 py-2 text-sm ${actionMessageType === "success" ? "border-[#D6EAD5] bg-[#F4FBF3] text-[#2F6E2D]" : "border-[#E8B4B0] bg-[#FFF3F1] text-[#A52A25]"}`}>
            {actionMessage}
          </div>
        ) : null}
        {missingIdentityCount > 0 ? (
          <div className="col-span-12 rounded-xl border border-[#E8B4B0] bg-[#FFF3F1] px-4 py-3 text-sm text-[#A52A25]">
            角色身份字段门禁未通过：仍有 {missingIdentityCount} 个角色缺少 skin_tone / ethnicity，当前版本不可定稿与导出。
            {failedIdentityCharacters.length > 0 ? (
              <span className="block mt-1 text-xs text-[#8A403A]">
                示例：{failedIdentityCharacters.slice(0, 3).map((x) => String(x.display_name || x.character_key || "未知角色")).join("，")}
              </span>
            ) : null}
          </div>
        ) : null}
        <aside className="col-span-12 lg:col-span-2 space-y-4 lg:sticky lg:top-[88px] h-fit">
          <Card className="p-3 bg-[#FEFCFA]">
            <p className="text-sm font-medium text-[#4A433D] mb-2">分镜 Lane 切换</p>
            <div className="space-y-2">
              <p className="text-xs text-[#8E8379]">竖屏版</p>
              {laneGroups.vertical.map((v) => (
                <button key={v.id} onClick={() => { setEpisodeNo(undefined); setActiveVersionId(v.id); }} className={`w-full text-left text-xs rounded-lg border px-2 py-1.5 ${activeVersionId === v.id ? "border-[#C8211B] bg-[#F8ECEA] text-[#A52A25]" : "border-[#E5DED7] bg-white text-[#6F665F]"}`}>
                  v{v.version_no} {v.is_final ? "(定稿)" : ""} · 源{v.source_novel_version_id ? `v${v.source_novel_version_id}` : "-"}
                </button>
              ))}
              <p className="text-xs text-[#8E8379] pt-1">横屏版</p>
              {laneGroups.horizontal.map((v) => (
                <button key={v.id} onClick={() => { setEpisodeNo(undefined); setActiveVersionId(v.id); }} className={`w-full text-left text-xs rounded-lg border px-2 py-1.5 ${activeVersionId === v.id ? "border-[#C8211B] bg-[#F8ECEA] text-[#A52A25]" : "border-[#E5DED7] bg-white text-[#6F665F]"}`}>
                  v{v.version_no} {v.is_final ? "(定稿)" : ""} · 源{v.source_novel_version_id ? `v${v.source_novel_version_id}` : "-"}
                </button>
              ))}
            </div>
          </Card>

          <Card className="p-3 bg-[#FEFCFA]">
            <p className="text-sm font-medium text-[#4A433D] mb-2">集数过滤</p>
            <Select
              value={String(episodeNo ?? "")}
              onValueChange={(v) => setEpisodeNo(v ? Number(v) : undefined)}
              className="h-9 px-2 py-2 text-sm"
              options={[
                { value: "", label: "全部" },
                ...episodeOptions.map((ep) => ({ value: String(ep), label: `第${ep}集` })),
              ]}
            />
          </Card>
        </aside>

        <section className="col-span-12 lg:col-span-7">
          <Card className="p-0 overflow-hidden bg-white">
            <div className="px-4 py-3 border-b border-[#E5DED7] bg-[#FAF7F4] flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-[#8E8379]">当前版本：{activeVersion ? `v${activeVersion.version_no} · ${formatStoryboardLane(activeVersion.lane)} · 源小说${activeVersion.source_novel_version_id ? `v${activeVersion.source_novel_version_id}` : "-"}` : "-"}</p>
              <p className="text-xs text-[#8E8379]">镜头数：{shots.length}</p>
            </div>
            <div className="px-4 py-3 border-b border-[#E5DED7] bg-[#FAF7F4] flex items-center gap-2">
              <button
                onClick={() => setActiveTab("shots")}
                className={`h-8 px-3 rounded-full text-sm border ${activeTab === "shots" ? "border-[#C8211B] bg-[#F8ECEA] text-[#A52A25]" : "border-[#E5DED7] bg-white text-[#6F665F]"}`}
              >
                分镜表
              </button>
              <button
                onClick={() => setActiveTab("characters")}
                className={`h-8 px-3 rounded-full text-sm border ${activeTab === "characters" ? "border-[#C8211B] bg-[#F8ECEA] text-[#A52A25]" : "border-[#E5DED7] bg-white text-[#6F665F]"}`}
              >
                角色主形象卡
              </button>
            </div>
            {loading ? <p className="p-4 text-sm text-[#7E756D]">加载中...</p> : null}
            {!loading && activeTab === "shots" && shots.length === 0 ? <p className="p-4 text-sm text-[#7E756D]">暂无镜头数据</p> : null}
            {!loading && activeTab === "shots" && shots.length > 0 ? (
              <div className="overflow-auto max-h-[72vh]">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-[#FDF9F6] border-b border-[#E5DED7] shadow-[0_1px_0_0_#EDE4DC]">
                    <tr className="text-left text-[#6F665F]">
                      <th className="px-3 py-2">集/场/镜</th>
                      <th className="px-3 py-2">景别</th>
                      <th className="px-3 py-2">动作</th>
                      <th className="px-3 py-2">台词</th>
                      <th className="px-3 py-2">导演意图</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shots.map((shot) => (
                      <tr key={shot.id} className="border-b border-[#F0EBE6] align-top hover:bg-[#FFFCFA] transition-colors">
                        <td className="px-3 py-2 text-xs text-[#5F5650] whitespace-nowrap">E{shot.episode_no} / S{shot.scene_no} / #{shot.shot_no}</td>
                        <td className="px-3 py-2 text-xs text-[#5F5650]">{shot.shot_size} · {shot.camera_move}</td>
                        <td className="px-3 py-2">
                          <textarea
                            className="w-full min-h-[70px] border border-[#E5DED7] rounded-md p-2 bg-white text-xs focus:border-[#C8211B] outline-none"
                            value={shot.action || ""}
                            disabled={isFinal}
                            onChange={(e) => {
                              const val = e.target.value;
                              setShots((prev) => prev.map((s) => s.id === shot.id ? { ...s, action: val } : s));
                            }}
                            onBlur={(e) => void onUpdateShot(shot.id, { action: e.target.value })}
                          />
                        </td>
                        <td className="px-3 py-2">
                          <textarea
                            className="w-full min-h-[70px] border border-[#E5DED7] rounded-md p-2 bg-white text-xs focus:border-[#C8211B] outline-none"
                            value={shot.dialogue || ""}
                            disabled={isFinal}
                            onChange={(e) => {
                              const val = e.target.value;
                              setShots((prev) => prev.map((s) => s.id === shot.id ? { ...s, dialogue: val } : s));
                            }}
                            onBlur={(e) => void onUpdateShot(shot.id, { dialogue: e.target.value })}
                          />
                        </td>
                        <td className="px-3 py-2">
                          <p className="text-xs text-[#3A3A3C]">{shot.motivation || "-"}</p>
                          <p className="text-[11px] text-[#8E8379] mt-1">走位：{shot.blocking || "-"}</p>
                          <p className="text-[11px] text-[#8E8379]">表演：{shot.performance_note || "-"}</p>
                          <p className="text-[11px] text-[#8E8379]">连续：{shot.continuity_anchor || "-"}</p>
                          {savingShotId === shot.id ? <p className="text-[11px] text-[#A52A25] mt-1">保存中...</p> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            {!loading && activeTab === "characters" && characterCards.length === 0 ? (
              <p className="p-4 text-sm text-[#7E756D]">暂无角色主形象卡，请先点击“生成人物主形象提示词”。</p>
            ) : null}
            {!loading && activeTab === "characters" && characterCards.length > 0 ? (
              <div className="p-4 space-y-3 max-h-[72vh] overflow-auto">
                {characterCards.map((item) => (
                  <div key={item.id} className="rounded-xl border border-[#E5DED7] bg-gradient-to-b from-white to-[#FFFCFA] p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-[#2D2926]">{item.display_name}</p>
                        <p className="text-xs text-[#8E8379]">{item.skin_tone} · {item.ethnicity}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <Button variant="secondary" size="sm" className="h-8 px-3" onClick={() => void copyText(item.master_prompt_text, `master-${item.id}`)}>
                          <Copy className="w-4 h-4 mr-1.5" />{copiedKey === `master-${item.id}` ? "已复制" : "复制主提示词"}
                        </Button>
                        <Button variant="secondary" size="sm" className="h-8 px-3" disabled={!item.negative_prompt_text?.trim()} onClick={() => void copyText(item.negative_prompt_text || "", `negative-${item.id}`)}>
                          <Copy className="w-4 h-4 mr-1.5" />{copiedKey === `negative-${item.id}` ? "已复制" : "复制负面词"}
                        </Button>
                        {!isFinal ? (
                          <Button
                            variant="secondary"
                            size="sm"
                            className="h-8 px-3"
                            loading={savingCardId === item.id}
                            onClick={() => void onUpdateCharacterCard(item)}
                          >
                            保存
                          </Button>
                        ) : null}
                      </div>
                    </div>
                    <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                      <label className="space-y-1 text-xs text-[#6A615A]">
                        <span>肤色（skin_tone）</span>
                        <input
                          value={item.skin_tone || ""}
                          disabled={isFinal}
                          onChange={(e) => {
                            const value = e.target.value;
                            setCharacterCards((prev) => prev.map((row) => (row.id === item.id ? { ...row, skin_tone: value } : row)));
                          }}
                          className="w-full h-9 rounded-md border border-[#E5DED7] px-2 bg-white text-sm focus:border-[#C8211B] outline-none"
                        />
                      </label>
                      <label className="space-y-1 text-xs text-[#6A615A]">
                        <span>族裔（ethnicity）</span>
                        <input
                          value={item.ethnicity || ""}
                          disabled={isFinal}
                          onChange={(e) => {
                            const value = e.target.value;
                            setCharacterCards((prev) => prev.map((row) => (row.id === item.id ? { ...row, ethnicity: value } : row)));
                          }}
                          className="w-full h-9 rounded-md border border-[#E5DED7] px-2 bg-white text-sm focus:border-[#C8211B] outline-none"
                        />
                      </label>
                    </div>

                    <label className="mt-3 block space-y-1 text-xs text-[#6A615A]">
                      <span>主提示词</span>
                      <textarea
                        className="w-full min-h-[92px] border border-[#E5DED7] rounded-md p-2 bg-white text-xs focus:border-[#C8211B] outline-none"
                        value={item.master_prompt_text || ""}
                        disabled={isFinal}
                        onChange={(e) => {
                          const value = e.target.value;
                          setCharacterCards((prev) => prev.map((row) => (row.id === item.id ? { ...row, master_prompt_text: value } : row)));
                        }}
                      />
                    </label>

                    <label className="mt-3 block space-y-1 text-xs text-[#6A615A]">
                      <span>负面词（Negative）</span>
                      <textarea
                        className="w-full min-h-[72px] border border-[#E5DED7] rounded-md p-2 bg-white text-xs focus:border-[#C8211B] outline-none"
                        value={item.negative_prompt_text || ""}
                        disabled={isFinal}
                        onChange={(e) => {
                          const value = e.target.value;
                          setCharacterCards((prev) =>
                            prev.map((row) => (row.id === item.id ? { ...row, negative_prompt_text: value } : row))
                          );
                        }}
                      />
                    </label>

                    <label className="mt-3 block space-y-1 text-xs text-[#6A615A]">
                      <span>一致性锚点（使用 `；` 分隔）</span>
                      <input
                        value={(item.consistency_anchors_json || []).join("；")}
                        disabled={isFinal}
                        onChange={(e) => {
                          const value = e.target.value;
                          const anchors = value
                            .split(/[；;,\n]/g)
                            .map((x) => x.trim())
                            .filter(Boolean);
                          setCharacterCards((prev) =>
                            prev.map((row) => (row.id === item.id ? { ...row, consistency_anchors_json: anchors } : row))
                          );
                        }}
                        className="w-full h-9 rounded-md border border-[#E5DED7] px-2 bg-white text-sm focus:border-[#C8211B] outline-none"
                      />
                    </label>
                  </div>
                ))}
              </div>
            ) : null}
          </Card>
        </section>

        <aside className="col-span-12 lg:col-span-3 space-y-4 lg:sticky lg:top-[88px] h-fit">
          {hasScoreData ? (
            <Card className="p-4 bg-[#FEFCFA]">
              <div className="flex items-center gap-2 mb-2">
                <Gauge className="w-4 h-4 text-[#C8211B]" />
                <p className="text-sm font-medium text-[#4A433D]">专业评分卡</p>
              </div>
              <div className="text-sm text-[#5E5650] space-y-2">
                <p>风格一致性：{typeof scoreCard.style_consistency_score === "number" ? (scoreCard.style_consistency_score * 100).toFixed(1) + "%" : "-"}</p>
                <p>可拍性风险：{typeof scoreCard.shot_density_risk === "number" ? (scoreCard.shot_density_risk * 100).toFixed(1) + "%" : "-"}</p>
                <p>字段完整率：{typeof scoreCard.completeness_rate === "number" ? (scoreCard.completeness_rate * 100).toFixed(1) + "%" : "-"}</p>
                {Object.keys(scoreCard.hook_score_episode || {}).length > 0 ? (
                  <div>
                    <p className="text-xs text-[#8E8379] mb-1">爆点评分（按集）</p>
                    <div className="max-h-28 overflow-auto pr-1 space-y-1">
                      {Object.entries(scoreCard.hook_score_episode || {}).map(([ep, val]) => (
                        <p key={ep} className="text-xs">第{ep}集：{Number(val).toFixed(0)}</p>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </Card>
          ) : null}

          {qualityGateReasons.length > 0 ? (
            <Card className="p-4 bg-[#FEFCFA]">
              <p className="text-sm font-medium text-[#4A433D] mb-2">质量门禁原因</p>
              <ul className="text-xs text-[#6F665F] space-y-1 list-disc list-inside">
                {qualityGateReasons.map((r: string) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </Card>
          ) : null}

          <Card className="p-4 text-xs text-[#7E756D] bg-[#FEFCFA]">
            <p>任务状态：{formatRunState(status?.run_state)}</p>
            <p>阶段：{formatStoryboardPhase(status?.current_phase)}</p>
            <p>进度：{status ? `${status.progress.toFixed(1)}%` : "-"}</p>
            <p>当前 Lane：{formatStoryboardLane(status?.current_lane || activeVersion?.lane)}</p>
            <p>ETA：{status?.eta_label || "-"}</p>
            <p className="mt-2">完成后请点击“确认定稿”再导出 CSV。</p>
          </Card>
        </aside>
      </div>

      <div className="max-w-[1520px] mx-auto px-4 pb-8">
        <Link href="/storyboards/create">
          <Button variant="secondary" size="sm" className="h-8 px-3">新建分镜项目</Button>
        </Link>
      </div>
      <ConfirmModal
        open={showCancelConfirm}
        onClose={() => setShowCancelConfirm(false)}
        onConfirm={() => {
          void runAction(
            "cancel",
            async () => {
              if (currentRunId) {
                await api.actionStoryboardRun(projectId, currentRunId, "cancel");
              } else {
                await api.cancelStoryboard(projectId, status?.task_id);
              }
              setShowCancelConfirm(false);
            },
            "任务已取消"
          );
        }}
        title="取消分镜任务"
        message="确定要取消当前分镜任务吗？"
        confirmText="确认取消"
        loading={actionBusy === "cancel"}
      />
      <ConfirmModal
        open={showFinalizeConfirm}
        onClose={() => setShowFinalizeConfirm(false)}
        onConfirm={() => {
          if (!activeVersion) return;
          void runAction(
            "finalize",
            async () => {
              await api.finalizeStoryboardVersion(projectId, activeVersion.id);
              setShowFinalizeConfirm(false);
            },
            "版本已定稿"
          );
        }}
        title="确认定稿"
        message="确认定稿后将无法继续编辑，是否继续？"
        confirmText="确认定稿"
        confirmVariant="primary"
        loading={actionBusy === "finalize"}
      />
    </main>
  );
}
