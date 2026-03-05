"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowLeft, CheckCircle2, LoaderCircle, Pause, Play, Square, XCircle } from "lucide-react";
import { api, Novel, GenerationStatus, ObservabilityPayload, VolumeGateReport, ClosureReport, RewriteRequest, getErrorMessage } from "@/lib/api";
import { formatRunState } from "@/lib/display";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { SectionTitle } from "@/components/ui/SectionTitle";
import { StatsCard } from "@/components/ui/StatsCard";
import { TopBar } from "@/components/ui/TopBar";
import { PageSpinner } from "@/components/ui/Spinner";

const PIPELINE_STEPS = [
  { id: "book_orchestrator", label: "总控编排", desc: "拆分卷任务并调度执行" },
  { id: "architect", label: "架构设计", desc: "规划故事结构与角色" },
  { id: "outliner", label: "大纲生成", desc: "生成章节大纲" },
  { id: "volume_planning", label: "分卷规划", desc: "根据上卷质量重规划本卷目标" },
  { id: "chapter_beats", label: "节拍卡", desc: "生成章节爽点/冲突/兑现节拍" },
  { id: "writer", label: "内容创作", desc: "撰写章节内容" },
  { id: "reviewer", label: "质量审核", desc: "检查内容质量" },
  { id: "finalizer", label: "最终处理", desc: "优化与定稿" },
];

const STEP_ALIASES: Record<string, string> = {
  queued: "book_orchestrator",
  book_planning: "book_orchestrator",
  volume_dispatch: "book_orchestrator",
  prewrite: "architect",
  outline_ready: "outliner",
  chapter_writing: "writer",
  chapter_review: "reviewer",
  chapter_finalizing: "finalizer",
  memory_update: "finalizer",
  closure_gate: "finalizer",
  bridge_chapter: "finalizer",
  tail_rewrite: "finalizer",
};

const SUBTASK_LABELS: Record<string, string> = {
  queued: "任务已入队",
  book_planning: "拆分卷任务",
  volume_dispatch: "调度卷任务",
  constitution: "生成创作宪法",
  specify_plan_tasks: "生成规格/计划/任务分解",
  full_outline_ready: "全书大纲已完成",
  outline_waiting_confirmation: "等待大纲确认",
  volume_replan: "分卷策略重规划",
  closure_gate: "收官完整性检查",
  bridge_chapter: "追加桥接章节",
  tail_rewrite: "尾章重写补完",
  context: "加载上下文",
  consistency: "一致性检查",
  chapter_blocked: "一致性未通过（跳过）",
  beats: "生成节拍卡",
  writer: "写作章节草稿",
  reviewer: "章节质量审校",
  revise: "按反馈修订",
  rollback_rerun: "回滚并重跑",
  finalizer: "章节定稿",
  memory_update: "更新记忆与摘要",
  chapter_done: "章节完成",
  final_book_review: "全书终审",
  done: "全书完成",
};

const CLOSURE_PHASE_LABELS: Record<string, string> = {
  expand: "展开期",
  converge: "聚合期",
  closing: "收官期",
  finale: "终章期",
};

const CLOSURE_ACTION_LABELS: Record<string, string> = {
  continue: "继续写作",
  bridge_chapter: "自动扩1章补完",
  rewrite_tail: "尾章重写补完",
  finalize: "进入终审",
  force_finalize: "强制终审",
};

const ACTIVE_GENERATION_STATUSES = new Set(["queued", "dispatching", "running"]);
const TERMINAL_GENERATION_STATUSES = new Set(["completed", "failed", "cancelled"]);

export default function ProgressPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const id = String(params.id);
  const taskId = searchParams.get("task_id");
  const rewriteRequestId = searchParams.get("rewrite_request_id");

  const [novel, setNovel] = useState<Novel | null>(null);
  const [status, setStatus] = useState<GenerationStatus | null>(null);
  const [rewriteStatus, setRewriteStatus] = useState<RewriteRequest | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [retrying, setRetrying] = useState(false);
  const [mutatingRunState, setMutatingRunState] = useState(false);
  const [gateReport, setGateReport] = useState<VolumeGateReport | null>(null);
  const [closureReport, setClosureReport] = useState<ClosureReport | null>(null);
  const [observability, setObservability] = useState<ObservabilityPayload | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getNovel(id).then(setNovel).catch(() => router.push("/novels"));
  }, [id, router]);

  // Polling fallback for status — merge with existing state to avoid overwriting SSE data
  useEffect(() => {
    if (rewriteRequestId) return;
    const pollStatus = async () => {
      try {
        const s = await api.getGenerationStatus(id, taskId || undefined);
        setStatus((prev) => {
          if (!prev) return s;
          const merged = { ...prev };
          for (const [k, v] of Object.entries(s)) {
            if (v !== undefined && v !== null && v !== 0 && v !== "") {
              (merged as Record<string, unknown>)[k] = v;
            }
          }
          if (s.status) merged.status = s.status;
          if (s.progress !== undefined) merged.progress = s.progress;
          return merged as typeof s;
        });
        setLoading(false);
      } catch (err) {
        console.error(err);
      }
    };

    pollStatus();
    const interval = setInterval(pollStatus, 3000);
    return () => clearInterval(interval);
  }, [id, taskId]);

  useEffect(() => {
    if (rewriteRequestId || !taskId || status?.status !== "failed") return;
    let disposed = false;
    const syncToLatestActiveTask = async () => {
      try {
        const tasks = await api.listGenerationTasks(id, 20);
        const current = tasks.find((t) => t.task_id === taskId);
        const latestActive = tasks.find((t) => ACTIVE_GENERATION_STATUSES.has(t.status));
        const currentTerminal = current ? TERMINAL_GENERATION_STATUSES.has(current.status) : true;
        if (!latestActive || !currentTerminal || latestActive.task_id === taskId || disposed) return;
        router.replace(`/novels/${id}/progress?task_id=${latestActive.task_id}`);
      } catch {
        // keep current task view when task list is temporarily unavailable
      }
    };
    void syncToLatestActiveTask();
    return () => {
      disposed = true;
    };
  }, [id, rewriteRequestId, router, status?.status, taskId]);

  useEffect(() => {
    if (!rewriteRequestId) return;
    const requestId = Number(rewriteRequestId);
    const pollRewrite = async () => {
      try {
        const s = await api.getRewriteStatus(id, requestId);
        setRewriteStatus(s);
        setLoading(false);
      } catch (err) {
        console.error(err);
      }
    };
    pollRewrite();
    const timer = setInterval(pollRewrite, 3000);
    return () => clearInterval(timer);
  }, [id, rewriteRequestId]);

  useEffect(() => {
    if (rewriteRequestId) return;
    const volumeNo = status?.volume_no;
    if (!volumeNo || volumeNo <= 0) return;
    api.getVolumeGateReport(id, volumeNo).then(setGateReport).catch(() => undefined);
  }, [id, status?.volume_no, rewriteRequestId]);

  useEffect(() => {
    if (rewriteRequestId) return;
    const loadClosure = () => {
      const nextTaskId = taskId || status?.task_id;
      return api.getClosureReport(id, nextTaskId || undefined).then(setClosureReport).catch(() => undefined);
    };
    loadClosure();
    const timer = setInterval(loadClosure, 5000);
    return () => clearInterval(timer);
  }, [id, rewriteRequestId, status?.task_id, taskId]);

  useEffect(() => {
    if (rewriteRequestId) return;
    const timer = setInterval(() => {
      api.getObservability(id).then(setObservability).catch(() => undefined);
    }, 5000);
    api.getObservability(id).then(setObservability).catch(() => undefined);
    return () => clearInterval(timer);
  }, [id, rewriteRequestId]);

  // SSE connection for real-time updates
  useEffect(() => {
    const streamTaskId = taskId || status?.task_id;
    if (rewriteRequestId || !streamTaskId) return;
    let disposed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const connectSSE = () => {
      if (disposed) return;
      const es = api.streamProgress(id, streamTaskId);
      eventSourceRef.current = es;

      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data && typeof data === "object" && "status" in data) {
            setStatus((prev) => ({ ...(prev || {}), ...(data as GenerationStatus) }));
          } else if (data.type === "status") {
            setStatus((prev) => ({ ...(prev || {}), ...(data.payload as GenerationStatus) }));
          } else if (data.type === "log") {
            setLogs((prev) => [...prev, data.payload]);
          }
        } catch {
          setLogs((prev) => [...prev, event.data]);
        }
      };

      es.onerror = () => {
        es.close();
        if (!disposed) {
          reconnectTimer = setTimeout(connectSSE, 5000);
        }
      };
    };

    connectSSE();

    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      eventSourceRef.current?.close();
    };
  }, [id, rewriteRequestId, status?.task_id, taskId]);

  // Auto-scroll logs
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const getCurrentStepIndex = useCallback(() => {
    const raw = status?.current_phase || status?.step;
    const step = raw ? STEP_ALIASES[raw] || raw : raw;
    if (!step) return -1;
    return PIPELINE_STEPS.findIndex((s) => s.id === step);
  }, [status]);

  const getStepStatus = useCallback(
    (index: number) => {
      const currentIndex = getCurrentStepIndex();
      if (status?.status === "completed") return "completed";
      if (status?.status === "failed" && index === currentIndex) return "failed";
      if (index < currentIndex) return "completed";
      if (index === currentIndex) return "active";
      return "pending";
    },
    [status, getCurrentStepIndex]
  );

  if (!novel) {
    return <PageSpinner />;
  }

  const isRewriteMode = Boolean(rewriteRequestId);
  const isComplete = isRewriteMode ? rewriteStatus?.status === "completed" : status?.status === "completed";
  const isFailed = isRewriteMode ? rewriteStatus?.status === "failed" : status?.status === "failed";
  const isAwaitingOutline = false;
  const runState = status?.run_state || status?.status;
  const effectiveTaskId = taskId || status?.task_id || undefined;
  const isRunning = isRewriteMode
    ? rewriteStatus?.status === "running" || rewriteStatus?.status === "queued"
    : status?.status === "queued" || status?.status === "dispatching" || status?.status === "running" || runState === "running";
  const isPaused = !isRewriteMode && runState === "paused";
  const activeSubtaskLabel = isRewriteMode
    ? (rewriteStatus?.message || "章节重写中")
    : status?.current_subtask?.label ||
      status?.subtask_label ||
      (status?.step ? SUBTASK_LABELS[status.step] || status.step : "");
  const closureState = status?.decision_state?.closure || closureReport?.state;
  const pacingState = status?.decision_state?.pacing;

  return (
    <main className="min-h-screen">
      <TopBar
        title="生成进度"
        subtitle={isRewriteMode ? `${novel.title} · 重写任务` : novel.title}
        backHref={`/novels/${id}`}
        icon={<ArrowLeft className="w-5 h-5" />}
        maxWidthClassName="max-w-[1280px]"
        actions={
          isComplete ? (
            <Link href={`/novels/${id}`}>
              <Button>
                查看小说
                <ArrowLeft className="w-4 h-4 ml-2 rotate-180" />
              </Button>
            </Link>
          ) : undefined
        }
      />

      <div className="max-w-[1280px] mx-auto px-4 py-6 space-y-8">
        {isComplete && (
          <div className="p-4 rounded-[12px] bg-[#E9F9EF] border border-[#CDEFD8] flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-white flex items-center justify-center">
              <CheckCircle2 className="w-5 h-5 text-[#18864B]" />
            </div>
            <div>
              <p className="font-medium text-[#18864B]">生成完成</p>
              <p className="text-sm text-[#18864B]">所有章节已成功生成</p>
            </div>
          </div>
        )}

        {isFailed && (
          <div className="p-4 rounded-[12px] bg-[#FFECEB] border border-[#FFD4D2] flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-white flex items-center justify-center">
              <XCircle className="w-5 h-5 text-[#C4372D]" />
            </div>
            <div className="flex-1">
              <p className="font-medium text-[#C4372D]">{isRewriteMode ? "重写失败" : "生成失败"}</p>
              <p className="text-sm text-[#C4372D]">{isRewriteMode ? (rewriteStatus?.error || "发生未知错误") : (status?.error || status?.message || "发生未知错误")}</p>
            </div>
            {isRewriteMode ? (
              <Button
                loading={retrying}
                onClick={async () => {
                  try {
                    if (!rewriteRequestId) return;
                    setRetrying(true);
                    await api.retryRewrite(id, Number(rewriteRequestId));
                    const next = await api.getRewriteStatus(id, Number(rewriteRequestId));
                    setRewriteStatus(next);
                  } catch (e) {
                    setLogs((prev) => [...prev, getErrorMessage(e, "重试提交失败，请稍后重试")]);
                  } finally {
                    setRetrying(false);
                  }
                }}
              >
                重写重试
              </Button>
            ) : (
              <Button
                loading={retrying}
                onClick={async () => {
                  try {
                    setRetrying(true);
                    const res = await api.retryGeneration(id, effectiveTaskId);
                    router.replace(`/novels/${id}/progress?task_id=${res.task_id}`);
                  } catch (e) {
                    setLogs((prev) => [...prev, getErrorMessage(e, "重试提交失败，请稍后重试")]);
                  } finally {
                    setRetrying(false);
                  }
                }}
              >
                失败重试
              </Button>
            )}
          </div>
        )}

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: [0.25, 0.1, 0.25, 1] }}>
        <Card className="p-6">
          <SectionTitle
            title="整体进度"
            right={<span className="text-xl font-semibold text-[#C8211B]">{Math.round(isRewriteMode ? (rewriteStatus?.progress || 0) : (status?.progress || 0))}%</span>}
          />

          <div className="h-3 bg-[#F6F3EF] rounded-full overflow-hidden mb-6">
            <div
              className={`h-full transition-all duration-500 rounded-full ${
                isFailed
                  ? "bg-[#C4372D]"
                  : isComplete
                  ? "bg-[#18864B]"
                  : "bg-[#C8211B]"
              }`}
              style={{ width: `${isRewriteMode ? (rewriteStatus?.progress || 0) : (status?.progress || 0)}%` }}
            />
          </div>

          {/* Chapter Progress */}
          {isRewriteMode && rewriteStatus ? (
            <>
              <p className="text-sm text-[#7E756D]">
                正在重写第 {rewriteStatus.current_chapter || rewriteStatus.rewrite_from_chapter} / {rewriteStatus.rewrite_to_chapter} 章
              </p>
              {isRunning && rewriteStatus.eta_label ? (
                <p className="text-xs text-[#7E756D] mt-1">预计剩余时间：{rewriteStatus.eta_label}</p>
              ) : null}
            </>
          ) : status?.current_chapter && status?.total_chapters ? (
            <p className="text-sm text-[#7E756D]">
              正在生成第 {status.current_chapter} / {status.total_chapters} 章
            </p>
          ) : null}
          {isRunning && status?.eta_label ? (
            <p className="text-xs text-[#7E756D] mt-1">预计剩余时间：{status.eta_label}</p>
          ) : null}
          {!isRewriteMode ? <p className="text-xs text-[#7E756D] mt-2">
            估算 Token：输入 {status?.token_usage_input || 0} / 输出 {status?.token_usage_output || 0}，预估费用 $
            {(status?.estimated_cost || 0).toFixed(4)}
          </p> : null}
          {!isRewriteMode ? (
            <div className="mt-4 flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={mutatingRunState || !isRunning || isPaused}
                onClick={async () => {
                  try {
                    setMutatingRunState(true);
                    await api.pauseGeneration(id, effectiveTaskId);
                    const next = await api.getGenerationStatus(id, effectiveTaskId);
                    setStatus(next);
                  } finally {
                    setMutatingRunState(false);
                  }
                }}
              >
                <Pause className="w-4 h-4 mr-1.5" />
                暂停
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={mutatingRunState || !isPaused}
                onClick={async () => {
                  try {
                    setMutatingRunState(true);
                    await api.resumeGeneration(id, effectiveTaskId);
                    const next = await api.getGenerationStatus(id, effectiveTaskId);
                    setStatus(next);
                  } finally {
                    setMutatingRunState(false);
                  }
                }}
              >
                <Play className="w-4 h-4 mr-1.5" />
                恢复
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={mutatingRunState || isComplete || isFailed}
                onClick={async () => {
                  if (!window.confirm("确定要取消当前生成任务吗？此操作不可撤销。")) return;
                  try {
                    setMutatingRunState(true);
                    await api.cancelGenerationByNovel(id, effectiveTaskId);
                    const next = await api.getGenerationStatus(id, effectiveTaskId);
                    setStatus(next);
                  } finally {
                    setMutatingRunState(false);
                  }
                }}
              >
                <Square className="w-4 h-4 mr-1.5" />
                取消
              </Button>
              <span className="text-xs text-[#8E8379]">运行状态：{formatRunState(runState)}</span>
            </div>
          ) : null}
          {status?.pacing_mode === "accelerated" || status?.pacing_mode === "closing_accelerated" ? (
            <p className="text-xs text-[#C8211B] mt-1">
              已启用自动节奏加速（连续低推进 {status?.low_progress_streak || 0} 章，信号 {Math.round((status?.progress_signal || 0) * 100)}%，原因 {(pacingState?.reasons || []).join(" / ") || "low_progress_streak"}）
            </p>
          ) : null}
          {isAwaitingOutline && effectiveTaskId && (
            <div className="mt-4">
              <Button
                onClick={async () => {
                  try {
                    await api.confirmOutline(id, effectiveTaskId);
                    const s = await api.getGenerationStatus(id, effectiveTaskId);
                    setStatus(s);
                    setLogs((prev) => [...prev, "已确认大纲，继续写作"]);
                  } catch (e) {
                    setLogs((prev) => [...prev, getErrorMessage(e, "确认失败，请重试")]);
                  }
                }}
              >
                确认大纲并继续
              </Button>
            </div>
          )}
        </Card>
        </motion.div>

        {!isRewriteMode ? <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <StatsCard
            label="当前章节"
            value={`${status?.current_chapter || 0} / ${status?.total_chapters || 0}`}
            hint="已进入章节循环后更新"
          />
          <StatsCard
            label="输入 Token"
            value={`${status?.token_usage_input || 0}`}
            hint="估算值"
          />
          <StatsCard
            label="输出 Token / 成本"
            value={`${status?.token_usage_output || 0}`}
            hint={`$${(status?.estimated_cost || 0).toFixed(4)}`}
          />
        </div> : null}

        {!isRewriteMode && (closureReport?.available || status?.decision_state?.closure) && closureState ? (
          <Card className="p-5">
            <SectionTitle
              title="收官状态"
              subtitle={`阶段：${CLOSURE_PHASE_LABELS[closureState.phase_mode || ""] || closureState.phase_mode || "-"} · 动作：${CLOSURE_ACTION_LABELS[closureState.action || ""] || closureState.action || "-"}${closureState.confidence ? ` · 置信度 ${Math.round(closureState.confidence * 100)}%` : ""}`}
            />
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-3">
              <StatsCard label="未闭环项" value={`${closureState.unresolved_count || 0}`} hint="must close" />
              <StatsCard
                label="收官分"
                value={`${Math.round((closureState.closure_score || 0) * 100)}%`}
                hint="closure score"
              />
              <StatsCard
                label="闭环覆盖率"
                value={`${Math.round((closureState.must_close_coverage || 0) * 100)}%`}
                hint={`阈值 ${Math.round(((closureState.closure_threshold || closureState.threshold || 0) as number) * 100)}%`}
              />
              <StatsCard
                label="章节弹性"
                value={`${closureState.min_total_chapters || 0} ~ ${closureState.max_total_chapters || 0}`}
                hint="允许范围"
              />
              <StatsCard
                label="桥接预算"
                value={`${closureState.bridge_budget_left || 0} / ${closureState.bridge_budget_total || 0}`}
                hint="剩余/总额度"
              />
              <StatsCard
                label="尾章重写"
                value={`${closureState.tail_rewrite_attempts || 0}`}
                hint="rewrite attempts"
              />
            </div>
            {(closureState.must_close_items || []).length > 0 ? (
              <div className="mt-3 rounded-[10px] border border-[#E4DFDA] bg-white px-3 py-2">
                <p className="text-xs text-[#8E8379] mb-1">当前优先回收项（Top 3）</p>
                <div className="space-y-1">
                  {(closureState.must_close_items || []).slice(0, 3).map((item, idx) => (
                    <p key={`${item.id || idx}`} className="text-sm text-[#5E5650] line-clamp-1">
                      {idx + 1}. {item.title || item.id || "未命名项"}
                    </p>
                  ))}
                </div>
              </div>
            ) : null}
            {(closureState.reasons || []).length > 0 ? (
              <p className="text-xs text-[#8E8379] mt-3">决策原因：{(closureState.reasons || []).join(" / ")}</p>
            ) : null}
          </Card>
        ) : null}
        {!isRewriteMode && !closureReport?.available && isRunning ? (
          <p className="text-xs text-[#8E8379]">收官状态准备中，生成进入中后段后会展示收官门禁数据。</p>
        ) : null}

        {!isRewriteMode && gateReport && (
          <Card className="p-6">
            <SectionTitle title={`第 ${gateReport.volume_no} 卷 Gate 报告`} subtitle={`结论: ${gateReport.verdict}`} />
            <div className="text-sm text-[#7E756D] space-y-1">
              {(gateReport.evidence_chain || []).slice(0, 6).map((e, idx) => (
                <p key={idx}>
                  {String(e.metric || "metric")} = {String(e.value ?? "")} (阈值 {String(e.threshold ?? "")})
                </p>
              ))}
            </div>
          </Card>
        )}

        {!isRewriteMode && observability && (
          <Card className="p-5">
            <SectionTitle title="运行提示" subtitle="仅展示和你操作直接相关的信息" />
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <StatsCard
                label="可恢复节点"
                value={`${observability.summary.checkpoints}`}
                hint="用于中断后继续执行"
              />
              <StatsCard
                label="风险卷"
                value={`${observability.summary.warning_or_fail_volumes}`}
                hint="需要重点检查的卷"
              />
            </div>
          </Card>
        )}

        {!isRewriteMode ? <Card className="p-5">
          <SectionTitle
            title="生成流程"
            subtitle={isRunning && activeSubtaskLabel ? `当前子任务：${activeSubtaskLabel}` : "单轴节点视图，节点下方显示详情"}
          />
          <div className="relative">
            <div className="absolute left-0 right-0 top-4 h-[2px] bg-[#E6E1DC]" />
            <div className="grid grid-cols-8 gap-2">
              {PIPELINE_STEPS.map((step, index) => {
                const stepStatus = getStepStatus(index);
                const showActiveSubtask = stepStatus === "active" && isRunning;
                return (
                  <div key={step.id} className="relative pt-0">
                    <div className="flex justify-center relative z-10">
                      <div
                        className={`
                          w-8 h-8 rounded-full flex items-center justify-center transition-all
                          ${stepStatus === "completed" ? "bg-[#E9F9EF] border-2 border-[#18864B]" : ""}
                          ${stepStatus === "active" ? "bg-[#F8ECEA] border-2 border-[#C8211B]" : ""}
                          ${stepStatus === "failed" ? "bg-[#FFECEB] border-2 border-[#C4372D]" : ""}
                          ${stepStatus === "pending" ? "bg-white border border-[rgba(60,60,67,0.20)]" : ""}
                        `}
                      >
                        {stepStatus === "completed" ? (
                          <CheckCircle2 className="w-4 h-4 text-[#18864B]" />
                        ) : stepStatus === "active" ? (
                          <LoaderCircle className="w-4 h-4 text-[#C8211B] animate-spin" />
                        ) : stepStatus === "failed" ? (
                          <XCircle className="w-4 h-4 text-[#C4372D]" />
                        ) : (
                          <span className="text-xs text-[#8E8E93]">{index + 1}</span>
                        )}
                      </div>
                    </div>

                    <div className="mt-3 px-1 text-center">
                      <p
                        className={`
                          text-[13px] font-medium leading-5
                          ${stepStatus === "completed" ? "text-[#18864B]" : ""}
                          ${stepStatus === "active" ? "text-[#C8211B]" : ""}
                          ${stepStatus === "failed" ? "text-[#C4372D]" : ""}
                          ${stepStatus === "pending" ? "text-[#6F665F]" : ""}
                        `}
                      >
                        {step.label}
                      </p>
                      <p className="text-[11px] leading-4 text-[#7E756D] mt-1 line-clamp-2">{step.desc}</p>
                      {showActiveSubtask && activeSubtaskLabel ? (
                        <div className="mt-1.5 inline-flex max-w-full items-center gap-1 rounded-full border border-[#EED1CC] bg-[#FDF1EF] px-2 py-0.5">
                          <LoaderCircle className="w-3 h-3 text-[#C8211B] animate-spin shrink-0" />
                          <span className="text-[11px] leading-4 text-[#A52A25] truncate" title={activeSubtaskLabel}>
                            {activeSubtaskLabel}
                          </span>
                        </div>
                      ) : null}
                      {showActiveSubtask && status?.message ? (
                        <p className="text-[10px] leading-4 text-[#9A9086] mt-1 line-clamp-1" title={status.message}>
                          {status.message}
                        </p>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </Card> : (
          <Card className="p-5">
            <SectionTitle title="重写流程" subtitle={activeSubtaskLabel} />
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <StatsCard
                label="重写区间"
                value={`第${rewriteStatus?.rewrite_from_chapter || 0}章 - 第${rewriteStatus?.rewrite_to_chapter || 0}章`}
                hint="自动级联"
              />
              <StatsCard
                label="当前章节"
                value={`第${rewriteStatus?.current_chapter || rewriteStatus?.rewrite_from_chapter || 0}章`}
                hint="实时更新"
              />
              <StatsCard
                label="任务状态"
                value={formatRunState(rewriteStatus?.status || "running")}
                hint={rewriteStatus?.error || rewriteStatus?.message || ""}
              />
            </div>
          </Card>
        )}

        {logs.length > 0 && (
          <Card className="p-6">
            <h2 className="text-lg font-semibold text-[#1F1B18] mb-4">生成日志</h2>
            <div className="bg-[#F6F3EF] rounded-[8px] p-4 max-h-64 overflow-y-auto font-mono text-xs">
              {logs.map((log, i) => (
                <div key={i} className="text-[#7E756D] py-0.5">
                  <span className="text-[#8E8E93] mr-2">[{String(i + 1).padStart(3, "0")}]</span>
                  {log}
                </div>
              ))}
              <div ref={logsEndRef} />
            </div>
          </Card>
        )}

      </div>
    </main>
  );
}
