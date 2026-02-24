"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowLeft, CheckCircle2, LoaderCircle, XCircle } from "lucide-react";
import { api, Novel, GenerationStatus } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { SectionTitle } from "@/components/ui/SectionTitle";
import { StatsCard } from "@/components/ui/StatsCard";
import { TopBar } from "@/components/ui/TopBar";

const PIPELINE_STEPS = [
  { id: "architect", label: "架构设计", desc: "规划故事结构与角色" },
  { id: "outliner", label: "大纲生成", desc: "生成章节大纲" },
  { id: "writer", label: "内容创作", desc: "撰写章节内容" },
  { id: "reviewer", label: "质量审核", desc: "检查内容质量" },
  { id: "finalizer", label: "最终处理", desc: "优化与定稿" },
];

export default function ProgressPage() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const id = String(params.id);
  const taskId = searchParams.get("task_id");

  const [novel, setNovel] = useState<Novel | null>(null);
  const [status, setStatus] = useState<GenerationStatus | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const eventSourceRef = useRef<EventSource | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getNovel(id).then(setNovel).catch(() => router.push("/novels"));
  }, [id, router]);

  // Polling fallback for status
  useEffect(() => {
    const pollStatus = async () => {
      try {
        const s = await api.getGenerationStatus(id, taskId || undefined);
        setStatus(s);
        setLoading(false);
      } catch (err) {
        console.error(err);
      }
    };

    pollStatus();
    const interval = setInterval(pollStatus, 3000);
    return () => clearInterval(interval);
  }, [id, taskId]);

  // SSE connection for real-time updates
  useEffect(() => {
    if (!taskId) return;

    const connectSSE = () => {
      const es = api.streamProgress(id, taskId);
      eventSourceRef.current = es;

      es.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "status") {
            setStatus(data.payload);
          } else if (data.type === "log") {
            setLogs((prev) => [...prev, data.payload]);
          }
        } catch {
          // Plain text log
          setLogs((prev) => [...prev, event.data]);
        }
      };

      es.onerror = () => {
        es.close();
        // Reconnect after delay
        setTimeout(connectSSE, 5000);
      };
    };

    connectSSE();

    return () => {
      eventSourceRef.current?.close();
    };
  }, [id, taskId]);

  // Auto-scroll logs
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const getCurrentStepIndex = useCallback(() => {
    const step = status?.current_phase || status?.step;
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
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin w-8 h-8 border-2 border-[#007AFF] border-t-transparent rounded-full" />
      </div>
    );
  }

  const isComplete = status?.status === "completed";
  const isFailed = status?.status === "failed";
  const isAwaitingOutline = status?.status === "awaiting_outline_confirmation";
  const isRunning = status?.status === "generating" || status?.status === "running";

  return (
    <main className="min-h-screen">
      <TopBar
        title="生成进度"
        subtitle={novel.title}
        backHref={`/novels/${id}`}
        icon={<ArrowLeft className="w-5 h-5" />}
        maxWidthClassName="max-w-4xl"
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

      <div className="max-w-4xl mx-auto px-4 py-8 space-y-8">
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
            <div>
              <p className="font-medium text-[#C4372D]">生成失败</p>
              <p className="text-sm text-[#C4372D]">{status?.error || "发生未知错误"}</p>
            </div>
          </div>
        )}

        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: [0.25, 0.1, 0.25, 1] }}>
        <Card className="p-6">
          <SectionTitle
            title="整体进度"
            right={<span className="text-2xl font-semibold text-[#007AFF]">{Math.round(status?.progress || 0)}%</span>}
          />

          <div className="h-3 bg-[#F2F2F4] rounded-full overflow-hidden mb-6">
            <div
              className={`h-full transition-all duration-500 rounded-full ${
                isFailed
                  ? "bg-[#C4372D]"
                  : isComplete
                  ? "bg-[#18864B]"
                  : "bg-[#007AFF]"
              }`}
              style={{ width: `${status?.progress || 0}%` }}
            />
          </div>

          {/* Chapter Progress */}
          {status?.current_chapter && status?.total_chapters && (
            <p className="text-sm text-[#6E6E73]">
              正在生成第 {status.current_chapter} / {status.total_chapters} 章
            </p>
          )}
          <p className="text-xs text-[#6E6E73] mt-2">
            估算 Token：输入 {status?.token_usage_input || 0} / 输出 {status?.token_usage_output || 0}，预估费用 $
            {(status?.estimated_cost || 0).toFixed(4)}
          </p>
          {isAwaitingOutline && taskId && (
            <div className="mt-4">
              <Button
                onClick={async () => {
                  try {
                    await api.confirmOutline(id, taskId);
                    const s = await api.getGenerationStatus(id, taskId);
                    setStatus(s);
                    setLogs((prev) => [...prev, "已确认大纲，继续写作"]);
                  } catch (e) {
                    setLogs((prev) => [...prev, "确认失败，请重试"]);
                  }
                }}
              >
                确认大纲并继续
              </Button>
            </div>
          )}
        </Card>
        </motion.div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
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
        </div>

        <Card className="p-6">
          <SectionTitle title="生成流程" subtitle="阶段化可观测，便于定位问题" />
          <div className="space-y-4">
            {PIPELINE_STEPS.map((step, index) => {
              const stepStatus = getStepStatus(index);
              return (
                <div key={step.id} className="flex items-start gap-4">
                  {/* Step Indicator */}
                  <div className="relative">
                    <div
                      className={`
                        w-10 h-10 rounded-full flex items-center justify-center transition-all
                        ${stepStatus === "completed" ? "bg-[#E9F9EF] border-2 border-[#18864B]" : ""}
                        ${stepStatus === "active" ? "bg-[#EAF3FF] border-2 border-[#007AFF]" : ""}
                        ${stepStatus === "failed" ? "bg-[#FFECEB] border-2 border-[#C4372D]" : ""}
                        ${stepStatus === "pending" ? "bg-white border border-[rgba(60,60,67,0.14)]" : ""}
                      `}
                    >
                      {stepStatus === "completed" ? (
                        <CheckCircle2 className="w-5 h-5 text-[#18864B]" />
                      ) : stepStatus === "active" ? (
                        <LoaderCircle className="w-4 h-4 text-[#007AFF] animate-spin" />
                      ) : stepStatus === "failed" ? (
                        <XCircle className="w-5 h-5 text-[#C4372D]" />
                      ) : (
                        <span className="text-sm text-[#8E8E93]">{index + 1}</span>
                      )}
                    </div>
                    {index < PIPELINE_STEPS.length - 1 && (
                      <div
                        className={`
                          absolute left-1/2 top-10 w-0.5 h-8 -translate-x-1/2
                          ${stepStatus === "completed" ? "bg-[#CDEFD8]" : "bg-[rgba(60,60,67,0.14)]"}
                        `}
                      />
                    )}
                  </div>

                  <div className="flex-1 pb-8">
                    <h3
                      className={`
                        font-medium
                        ${stepStatus === "completed" ? "text-[#18864B]" : ""}
                        ${stepStatus === "active" ? "text-[#007AFF]" : ""}
                        ${stepStatus === "failed" ? "text-[#C4372D]" : ""}
                        ${stepStatus === "pending" ? "text-[#6E6E73]" : ""}
                      `}
                    >
                      {step.label}
                    </h3>
                    <p className="text-sm text-[#6E6E73]">{step.desc}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </Card>

        {logs.length > 0 && (
          <Card className="p-6">
            <h2 className="text-lg font-semibold text-[#1D1D1F] mb-4">生成日志</h2>
            <div className="bg-[#F2F2F4] rounded-[8px] p-4 max-h-64 overflow-y-auto font-mono text-xs">
              {logs.map((log, i) => (
                <div key={i} className="text-[#6E6E73] py-0.5">
                  <span className="text-[#8E8E93] mr-2">[{String(i + 1).padStart(3, "0")}]</span>
                  {log}
                </div>
              ))}
              <div ref={logsEndRef} />
            </div>
          </Card>
        )}

        {status?.message && (
          <div className="text-center text-[#6E6E73] text-sm">{status.message}</div>
        )}
      </div>
    </main>
  );
}
