export function formatUserRole(role?: string | null): string {
  const value = String(role || "").toLowerCase();
  if (value === "admin") return "管理员";
  if (value === "user") return "普通用户";
  return role || "-";
}

export function formatUserStatus(status?: string | null): string {
  const value = String(status || "").toLowerCase();
  if (value === "active") return "正常";
  if (value === "pending_activation") return "待激活";
  if (value === "disabled") return "已禁用";
  return status || "-";
}

export function formatRunState(runState?: string | null): string {
  const value = String(runState || "").toLowerCase();
  const map: Record<string, string> = {
    queued: "排队中",
    dispatching: "调度中",
    submitted: "已提交",
    running: "运行中",
    retrying: "重试中",
    paused: "已暂停",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    skipped: "已跳过",
  };
  return map[value] || runState || "-";
}

export function formatStoryboardLane(lane?: string | null): string {
  const value = String(lane || "").toLowerCase();
  if (value === "vertical_feed") return "竖屏版";
  if (value === "horizontal_cinematic") return "横屏版";
  return lane || "-";
}

export function formatStoryboardPhase(phase?: string | null): string {
  const value = String(phase || "").toLowerCase();
  const map: Record<string, string> = {
    queued: "任务排队",
    planning: "规划中",
    generating: "生成中",
    running: "处理中",
    quality_gate: "质量门禁",
    finalizing: "定稿中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    paused: "已暂停",
  };
  return map[value] || phase || "-";
}

export function formatPlanKey(planKey?: string | null): string {
  const value = String(planKey || "").toLowerCase();
  if (value === "free") return "免费版";
  if (value === "pro") return "专业版";
  if (value === "enterprise") return "企业版";
  return planKey || "-";
}

export function formatUsageSource(source?: string | null): string {
  const value = String(source || "").toLowerCase();
  if (value === "generation") return "小说生成";
  if (value === "rewrite") return "章节重写";
  if (value === "storyboard") return "导演分镜";
  return source || "-";
}

export function formatNovelStatus(status?: string | null): string {
  const value = String(status || "").toLowerCase();
  const map: Record<string, string> = {
    draft: "草稿",
    generating: "生成中",
    awaiting_outline_confirmation: "待确认大纲",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    paused: "已暂停",
  };
  return map[value] || "未知状态";
}
