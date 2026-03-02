const BASE = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/+$/, "");

const AUTH_TOKEN_KEY = "auth_token";

// Types
export interface Novel {
  id: string;
  title: string;
  genre?: string;
  style?: string;
  status: string;
  target_language?: string;
  created_at: string;
  updated_at?: string;
  config?: Record<string, unknown>;
}

export interface Chapter {
  id: number;
  chapter_num: number;
  title?: string;
  content?: string;
  status?: string;
  language_quality_score?: number;
  language_quality_report?: string;
  word_count?: number;
  created_at?: string;
}

export interface CharacterProfile {
  id: number;
  novel_id: string;
  character_key: string;
  display_name: string;
  gender_presentation?: string;
  age_band?: string;
  skin_tone?: string;
  ethnicity?: string;
  body_type?: string;
  face_features?: string;
  hair_style?: string;
  hair_color?: string;
  eye_color?: string;
  wardrobe_base_style?: string;
  signature_items_json: string[];
  visual_do_not_change_json: string[];
  evidence_json: Array<Record<string, unknown>>;
  confidence: number;
  updated_chapter_num?: number;
  created_at: string;
  updated_at?: string;
}

export interface NovelVersion {
  id: number;
  novel_id: string;
  version_no: number;
  parent_version_id?: number | null;
  status: string;
  is_default: boolean;
  created_at: string;
  updated_at?: string;
}

export interface RewriteAnnotationInput {
  chapter_num: number;
  start_offset?: number;
  end_offset?: number;
  selected_text?: string;
  issue_type?: "bug" | "continuity" | "style" | "pace" | "other";
  instruction: string;
  priority?: "must" | "should" | "nice";
  metadata?: Record<string, unknown>;
}

export interface RewriteRequest {
  id: number;
  novel_id: string;
  base_version_id: number;
  target_version_id: number;
  task_id?: string;
  status: "queued" | "submitted" | "running" | "paused" | "completed" | "failed" | "cancelled";
  rewrite_from_chapter: number;
  rewrite_to_chapter: number;
  current_chapter?: number;
  progress: number;
  eta_seconds?: number;
  eta_label?: string;
  message?: string;
  error?: string;
  created_at: string;
  updated_at?: string;
}

export interface ChapterProgress {
  chapter_num: number;
  title?: string;
  status: "pending" | "generating" | "completed";
}

export interface GenerationStatus {
  status: string;
  trace_id?: string;
  run_state?: string;
  progress: number;
  current_phase?: string;
  step?: string;
  subtask_key?: string;
  subtask_label?: string;
  subtask_progress?: number;
  current_subtask?: {
    key?: string;
    label?: string;
    progress?: number;
  };
  current_chapter?: number;
  total_chapters?: number;
  volume_no?: number;
  volume_size?: number;
  pacing_mode?: string;
  low_progress_streak?: number;
  progress_signal?: number;
  eta_seconds?: number;
  eta_label?: string;
  decision_state?: {
    closure?: ClosureState;
    pacing?: {
      mode?: string;
      low_progress_streak?: number;
      progress_signal?: number;
      reasons?: string[];
    };
    quality?: Record<string, unknown>;
  };
  token_usage_input?: number;
  token_usage_output?: number;
  estimated_cost?: number;
  message?: string;
  error?: string;
  last_error?: {
    code?: string;
    category?: string;
    retryable?: boolean;
    message?: string;
  };
}

export interface GenerationTaskItem {
  task_id: string;
  trace_id?: string;
  status: string;
  run_state?: string;
  current_chapter?: number;
  total_chapters?: number;
  progress?: number;
  message?: string;
  error?: string;
  error_code?: string;
  error_category?: string;
  retryable?: boolean;
  updated_at?: string;
}

export interface VolumeGateReport {
  volume_no: number;
  verdict: string;
  metrics: Record<string, unknown>;
  evidence_chain: Array<Record<string, unknown>>;
  checkpoint_id?: number | null;
  checkpoint_state?: Record<string, unknown>;
  created_at?: string;
}

export interface ClosureItem {
  type?: string;
  id?: string;
  title?: string;
  introduced_chapter?: number;
}

export interface ClosureState {
  generated_chapters?: number;
  target_chapters?: number;
  min_total_chapters?: number;
  max_total_chapters?: number;
  remaining_chapters?: number;
  remaining_ratio?: number;
  phase_mode?: string;
  unresolved_count?: number;
  closure_score?: number;
  must_close_coverage?: number;
  closure_threshold?: number;
  threshold?: number;
  tail_rewrite_attempts?: number;
  bridge_attempts?: number;
  bridge_budget_total?: number;
  bridge_budget_left?: number;
  confidence?: number;
  reasons?: string[];
  action?: string;
  must_close_items?: ClosureItem[];
}

export interface ClosureReport {
  novel_id: string;
  task_id?: string | null;
  available: boolean;
  chapter_num?: number;
  volume_no?: number;
  state?: ClosureState;
  message?: string;
  created_at?: string;
}

export interface NovelFeedback {
  id: number;
  chapter_num?: number | null;
  volume_no?: number | null;
  feedback_type: string;
  rating?: number | null;
  tags: string[];
  comment?: string;
  created_at?: string;
}

export interface ObservabilityPayload {
  summary: {
    quality_reports: number;
    checkpoints: number;
    feedback_count: number;
    warning_or_fail_volumes: number;
    closure_action_distribution?: Record<string, number>;
    closure_action_oscillation_rate?: number;
    abrupt_ending_score?: number;
    abrupt_ending_risk?: boolean;
    abrupt_ending_reasons?: string[];
  };
  quality_reports: Array<Record<string, unknown>>;
  checkpoints: Array<Record<string, unknown>>;
  feedback: NovelFeedback[];
}

export interface CreateNovelData {
  title: string;
  genre?: string;
  style?: string;
  audience?: string;
  strategy?: string;
  target_language?: string;
  config?: Record<string, unknown>;
}

export interface IdeaFrameworkRequest {
  title: string;
  target_language?: string;
  genre?: string;
  style?: string;
  strategy?: string;
}

export interface IdeaFrameworkResponse {
  title: string;
  one_liner: string;
  premise: string;
  conflict: string;
  hook: string;
  selling_point: string;
  editable_framework: string;
}

export interface AuthUser {
  uuid: string;
  email: string;
  role: string;
  status: string;
  email_verified: boolean;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

export interface AdminUserListItem {
  uuid: string;
  email: string;
  role: string;
  status: string;
  created_at: string;
  last_login_at?: string | null;
}

export interface AccountQuota {
  plan_key: string;
  max_concurrent_tasks: number;
  monthly_chapter_limit: number;
  monthly_token_limit: number;
  used_chapters: number;
  used_tokens: number;
  remaining_chapters: number;
  remaining_tokens: number;
  month: string;
}

export interface UsageLedgerItem {
  task_id: string;
  source: string;
  input_tokens: number;
  output_tokens: number;
  chapters_generated: number;
  estimated_cost: number;
  created_at: string;
}

export interface NotificationItem {
  id: string;
  type: string;
  title: string;
  message: string;
  created_at: string;
}

export type StoryboardLane = "vertical_feed" | "horizontal_cinematic";

export interface StoryboardProject {
  id: number;
  uuid: string;
  novel_id: string;
  novel_title?: string;
  status: "draft" | "generating" | "ready" | "finalized" | "failed";
  target_episodes: number;
  target_episode_seconds: number;
  style_profile?: string;
  mode?: "quick" | "professional";
  genre_style_key?: string;
  director_style_key?: string;
  style_recommendations?: StoryboardStyleRecommendationItem[];
  professional_mode: boolean;
  audience_goal?: string;
  output_lanes: StoryboardLane[];
  active_lane: StoryboardLane;
  created_at: string;
  updated_at?: string;
}

export interface StoryboardVersion {
  id: number;
  storyboard_project_id: number;
  source_novel_version_id: number | null;
  version_no: number;
  parent_version_id?: number | null;
  lane: StoryboardLane;
  status: "draft" | "generating" | "completed" | "failed";
  is_default: boolean;
  is_final: boolean;
  quality_report_json: {
    style_consistency_score?: number;
    hook_score_episode?: Record<string, number>;
    quality_gate_reasons?: string[];
    completeness_rate?: number;
    shot_density_risk?: number;
    rewrite_suggestions?: string[];
    character_prompt_phase?: string;
    character_profiles_count?: number;
    missing_identity_fields_count?: number;
    failed_identity_characters?: Array<Record<string, unknown>>;
  };
  created_at: string;
  updated_at?: string;
}

export interface StoryboardShot {
  id: number;
  storyboard_version_id: number;
  episode_no: number;
  scene_no: number;
  shot_no: number;
  location?: string;
  time_of_day?: string;
  shot_size?: string;
  camera_angle?: string;
  camera_move?: string;
  duration_sec: number;
  characters_json: string[];
  action?: string;
  dialogue?: string;
  emotion_beat?: string;
  transition?: string;
  sound_hint?: string;
  production_note?: string;
  blocking?: string;
  motivation?: string;
  performance_note?: string;
  continuity_anchor?: string;
  created_at: string;
  updated_at?: string;
}

export interface StoryboardTaskStatus {
  storyboard_project_id: number;
  task_id?: string;
  status: string;
  run_state?: string;
  current_phase?: string;
  current_lane?: StoryboardLane;
  progress: number;
  current_episode?: number;
  eta_seconds?: number;
  eta_label?: string;
  message?: string;
  error?: string;
  error_code?: string;
  error_category?: string;
  retryable?: boolean;
  style_consistency_score?: number;
  hook_score_episode?: Record<string, number>;
  quality_gate_reasons?: string[];
  character_prompt_phase?: string;
  character_profiles_count?: number;
  missing_identity_fields_count?: number;
  failed_identity_characters?: Array<Record<string, unknown>>;
}

export interface StoryboardCharacterPrompt {
  id: number;
  storyboard_project_id: number;
  storyboard_version_id: number;
  lane: StoryboardLane;
  character_key: string;
  display_name: string;
  skin_tone: string;
  ethnicity: string;
  master_prompt_text: string;
  negative_prompt_text?: string;
  style_tags_json: string[];
  consistency_anchors_json: string[];
  quality_score?: number;
  created_at: string;
  updated_at?: string;
}

export interface StoryboardCreateData {
  novel_id: string;
  target_episodes: number;
  target_episode_seconds: number;
  style_profile?: string;
  mode?: "quick" | "professional";
  genre_style_key?: string;
  director_style_key?: string;
  auto_style_recommendation?: boolean;
  output_lanes?: StoryboardLane[];
  professional_mode?: boolean;
  audience_goal?: string;
  copyright_assertion: boolean;
}

export interface StoryboardGenerateResponse {
  task_id: string;
  storyboard_project_id: number;
  created_version_ids: number[];
}

export interface StoryboardStylePresetItem {
  key: string;
  label: string;
  description: string;
  tags?: string[];
  camera_notes?: string[];
}

export interface StoryboardStyleRecommendationItem {
  genre_style_key: string;
  genre_style_label: string;
  director_style_key: string;
  director_style_label: string;
  confidence: number;
  reason: string;
}

export interface UpdateNovelData {
  title?: string;
  genre?: string;
  style?: string;
  config?: Record<string, unknown>;
}

export interface PresetCategory {
  [key: string]: {
    id: string;
    label: string;
    description?: string;
    chapter_target?: number;
    strategy?: string;
  }[];
}

// API Error class
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public errorCode?: string,
    public retryable?: boolean,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ParsedApiError {
  message: string;
  errorCode?: string;
  retryable?: boolean;
}

export function getErrorMessage(error: unknown, fallback = "请求失败，请稍后重试"): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message.trim()) return error.message;
  return fallback;
}

function parseApiError(status: number, rawText: string): ParsedApiError {
  const fallback = rawText || `HTTP ${status}`;
  const text = (rawText || "").trim();
  if (!text) return { message: humanizeApiErrorMessage(fallback) };

  try {
    const data = JSON.parse(text) as Record<string, unknown>;
    const detail = data.detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const detailObj = detail as { message?: string; error_code?: string; retryable?: boolean };
      const message =
        typeof detailObj.message === "string" && detailObj.message.trim()
          ? humanizeApiErrorMessage(detailObj.message.trim())
          : undefined;
      const errorCode = typeof detailObj.error_code === "string" && detailObj.error_code.trim() ? detailObj.error_code.trim() : undefined;
      const retryable = typeof detailObj.retryable === "boolean" ? detailObj.retryable : undefined;
      if (message) return { message, errorCode, retryable };
    }

    if (typeof detail === "string" && detail.trim()) {
      return { message: humanizeApiErrorMessage(detail.trim()) };
    }

    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string; message?: string } | undefined;
      if (typeof first?.msg === "string" && first.msg.trim()) {
        return { message: humanizeApiErrorMessage(first.msg.trim()) };
      }
      if (typeof first?.message === "string" && first.message.trim()) {
        return { message: humanizeApiErrorMessage(first.message.trim()) };
      }
    }

    const message =
      typeof data.message === "string" && data.message.trim()
        ? humanizeApiErrorMessage(data.message.trim())
        : typeof data.error === "string" && data.error.trim()
        ? humanizeApiErrorMessage(data.error.trim())
        : undefined;
    const errorCode = typeof data.error_code === "string" && data.error_code.trim() ? data.error_code.trim() : undefined;
    const retryable = typeof data.retryable === "boolean" ? data.retryable : undefined;
    if (message) return { message, errorCode, retryable };
  } catch {
    // non-JSON payload
  }

  return { message: humanizeApiErrorMessage(fallback) };
}

function humanizeApiErrorMessage(message: string): string {
  const normalized = (message || "").trim();
  const lower = normalized.toLowerCase();
  if (!normalized) return "请求失败，请稍后重试";

  if (lower.includes("email not verified")) return "邮箱未激活，请先完成邮箱激活后再登录";
  if (lower.includes("email already registered")) return "该邮箱已注册，请直接登录";
  if (lower.includes("invalid email or password")) return "邮箱或密码错误";
  if (lower.includes("invalid email")) return "邮箱格式不正确";
  if (lower.includes("account temporarily locked")) return "账号已被临时锁定，请稍后再试";
  if (lower.includes("user disabled")) return "账号已被禁用，请联系管理员";
  if (lower.includes("user inactive")) return "账号未激活，请先完成激活后再登录";
  if (lower.includes("user disabled or inactive")) return "账号未激活或已被禁用";
  if (lower.includes("mail service is not configured")) return "系统邮件服务未配置，请联系管理员";
  if (lower.includes("missing token")) return "登录已失效，请重新登录";
  if (lower.includes("invalid token")) return "登录凭证无效，请重新登录";

  return normalized;
}

// Fetch wrapper with error handling
async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const token =
    typeof window !== "undefined" ? window.localStorage.getItem(AUTH_TOKEN_KEY) : null;

  const url = `${BASE}${path}`;

  let res: Response;
  try {
    res = await fetch(url, {
      ...options,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options?.headers,
      },
    });
  } catch (error) {
    const message = error instanceof Error && error.message.trim() ? error.message.trim() : "Network request failed";
    throw new ApiError(0, `网络请求失败：${message}（${url}）`);
  }

  if (!res.ok) {
    const text = await res.text();
    if (res.status === 401 && typeof window !== "undefined") {
      window.localStorage.removeItem(AUTH_TOKEN_KEY);
      const current = `${window.location.pathname}${window.location.search || ""}`;
      if (!window.location.pathname.startsWith("/auth")) {
        const next = encodeURIComponent(current);
        window.location.href = `/auth/login?next=${next}`;
      }
    }
    const parsed = parseApiError(res.status, text);
    const message = res.status >= 500 ? `${parsed.message}（${path}）` : parsed.message;
    throw new ApiError(res.status, message, parsed.errorCode, parsed.retryable);
  }

  // Handle empty responses
  const text = await res.text();
  if (!text) return {} as T;

  return JSON.parse(text) as T;
}

// API methods
export const api = {
  setAuthToken: (token: string | null) => {
    if (typeof window === "undefined") return;
    if (token) {
      window.localStorage.setItem(AUTH_TOKEN_KEY, token);
    } else {
      window.localStorage.removeItem(AUTH_TOKEN_KEY);
    }
  },

  getAuthToken: () => {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(AUTH_TOKEN_KEY);
  },

  // Auth
  register: (data: { email: string; password: string }) =>
    fetchApi<{ ok?: boolean; message?: string; access_token?: string; user?: AuthUser }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  login: (data: { email: string; password: string }) =>
    fetchApi<AuthResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  logout: () =>
    fetchApi<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),

  me: () =>
    fetchApi<{ user: AuthUser }>("/api/auth/me"),

  getQuota: () =>
    fetchApi<AccountQuota>("/api/account/quota"),

  getUsageLedger: (limit = 50) =>
    fetchApi<UsageLedgerItem[]>(`/api/account/ledger?limit=${encodeURIComponent(limit)}`),

  getNotifications: (limit = 30) =>
    fetchApi<NotificationItem[]>(`/api/account/notifications?limit=${encodeURIComponent(limit)}`),

  requestVerifyEmail: (email: string) =>
    fetchApi<{ ok: boolean; message?: string }>("/api/auth/verify-email/request", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  confirmVerifyEmail: (token: string) =>
    fetchApi<{ ok: boolean }>("/api/auth/verify-email/confirm", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),

  forgotPassword: (email: string) =>
    fetchApi<{ ok: boolean }>("/api/auth/password/forgot", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  resetPassword: (token: string, newPassword: string) =>
    fetchApi<{ ok: boolean }>("/api/auth/password/reset", {
      method: "POST",
      body: JSON.stringify({ token, new_password: newPassword }),
    }),

  changePassword: (currentPassword: string, newPassword: string) =>
    fetchApi<{ ok: boolean }>("/api/auth/password/change", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),

  getAdminUsers: () =>
    fetchApi<AdminUserListItem[]>("/api/admin/users"),

  listNovels: (params?: { skip?: number; limit?: number; user_uuid?: string; only_mine?: boolean }) => {
    const qs = new URLSearchParams();
    if (params?.skip !== undefined) qs.set("skip", String(params.skip));
    if (params?.limit !== undefined) qs.set("limit", String(params.limit));
    if (params?.user_uuid) qs.set("user_uuid", params.user_uuid);
    if (params?.only_mine) qs.set("only_mine", "true");
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return fetchApi<Novel[]>(`/api/novels${suffix}`);
  },

  createNovel: (data: CreateNovelData) =>
    fetchApi<{ id: string }>("/api/novels", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  generateIdeaFramework: (data: IdeaFrameworkRequest) =>
    fetchApi<IdeaFrameworkResponse>("/api/novels/idea-framework", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getNovel: (id: string) => fetchApi<Novel>(`/api/novels/${id}`),

  updateNovel: (id: string, data: UpdateNovelData) =>
    fetchApi<Novel>(`/api/novels/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  deleteNovel: (id: string) =>
    fetchApi<void>(`/api/novels/${id}`, { method: "DELETE" }),

  // Chapters
  getVersions: (novelId: string) =>
    fetchApi<NovelVersion[]>(`/api/novels/${novelId}/versions`),

  activateVersion: (novelId: string, versionId: number) =>
    fetchApi<{ ok: boolean; active_version_id: number }>(`/api/novels/${novelId}/versions/${versionId}/activate`, {
      method: "POST",
    }),

  getChapters: (novelId: string, versionId?: number) =>
    fetchApi<Chapter[]>(`/api/novels/${novelId}/chapters${versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""}`),

  getChapter: (novelId: string, chapterNum: number, versionId?: number) =>
    fetchApi<Chapter>(`/api/novels/${novelId}/chapters/${chapterNum}${versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""}`),

  getChapterProgress: (novelId: string) =>
    fetchApi<ChapterProgress[]>(`/api/novels/${novelId}/chapter-progress`),

  getCharacterProfiles: (novelId: string) =>
    fetchApi<CharacterProfile[]>(`/api/novels/${novelId}/character-profiles`),

  updateChapter: (novelId: string, chapterNum: number, data: { title?: string; content?: string }, versionId?: number) =>
    fetchApi<Chapter>(`/api/novels/${novelId}/chapters/${chapterNum}${versionId ? `?version_id=${encodeURIComponent(versionId)}` : ""}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  createRewriteRequest: (
    novelId: string,
    data: {
      base_version_id: number;
      annotations: RewriteAnnotationInput[];
    }
  ) =>
    fetchApi<RewriteRequest>(`/api/novels/${novelId}/rewrite-requests`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getRewriteStatus: (novelId: string, requestId: number) =>
    fetchApi<RewriteRequest>(`/api/novels/${novelId}/rewrite-requests/${requestId}/status`),

  retryRewrite: (novelId: string, requestId: number) =>
    fetchApi<RewriteRequest>(`/api/novels/${novelId}/rewrite-requests/${requestId}/retry`, {
      method: "POST",
    }),

  // Generation
  getGenerationStatus: (novelId: string, taskId?: string) =>
    fetchApi<GenerationStatus>(
      `/api/novels/${novelId}/generation/status${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`
    ),

  submitGeneration: (novelId: string, numChapters?: number, startChapter?: number, requireOutlineConfirmation?: boolean) =>
    fetchApi<{ task_id: string }>(`/api/novels/${novelId}/generate`, {
      method: "POST",
      body: JSON.stringify({
        num_chapters: numChapters ?? 1,
        start_chapter: startChapter ?? 1,
        require_outline_confirmation: requireOutlineConfirmation ?? false,
      }),
    }),

  cancelGeneration: (novelId: string, taskId: string) =>
    fetchApi<{ ok: boolean }>(`/api/novels/${novelId}/generation/${taskId}`, {
      method: "DELETE",
    }),

  pauseGeneration: (novelId: string, taskId?: string) =>
    fetchApi<{ ok: boolean; task_id: string; run_state: string }>(
      `/api/novels/${novelId}/generation/pause${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`,
      { method: "POST" }
    ),

  resumeGeneration: (novelId: string, taskId?: string) =>
    fetchApi<{ ok: boolean; task_id: string; run_state: string }>(
      `/api/novels/${novelId}/generation/resume${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`,
      { method: "POST" }
    ),

  cancelGenerationByNovel: (novelId: string, taskId?: string) =>
    fetchApi<{ ok: boolean; task_id: string; run_state: string }>(
      `/api/novels/${novelId}/generation/cancel${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`,
      { method: "POST" }
    ),

  listGenerationTasks: (novelId: string, limit = 20) =>
    fetchApi<GenerationTaskItem[]>(`/api/novels/${novelId}/generation/tasks?limit=${encodeURIComponent(limit)}`),

  confirmOutline: (novelId: string, taskId: string) =>
    fetchApi<{ ok: boolean; message: string }>(`/api/novels/${novelId}/generation/${taskId}/confirm-outline`, {
      method: "POST",
    }),

  retryGeneration: (novelId: string, taskId?: string) =>
    fetchApi<{ task_id: string; novel_id: string; status: string }>(`/api/novels/${novelId}/generation/retry`, {
      method: "POST",
      body: JSON.stringify({ task_id: taskId || null }),
    }),

  // Presets
  getPresets: () => fetchApi<PresetCategory>("/api/presets"),

  getPresetsByCategory: (category: string) =>
    fetchApi<PresetCategory[string]>(`/api/presets/${category}`),

  // Export
  getExportUrl: (novelId: string, format: 'txt' | 'md' | 'zip') =>
    `${BASE}/api/novels/${novelId}/export?format=${format}`,

  // SSE Progress stream
  streamProgress: (novelId: string, taskId: string) => {
    const url = `${BASE}/api/novels/${novelId}/generation/progress?task_id=${taskId}`;
    return new EventSource(url);
  },

  getVolumeGateReport: (novelId: string, volumeNo: number) =>
    fetchApi<VolumeGateReport>(`/api/novels/${novelId}/volumes/${volumeNo}/gate-report`),

  getClosureReport: (novelId: string, taskId?: string) =>
    fetchApi<ClosureReport>(
      `/api/novels/${novelId}/closure-report${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`
    ),

  listFeedback: (novelId: string) =>
    fetchApi<NovelFeedback[]>(`/api/novels/${novelId}/feedback`),

  createFeedback: (
    novelId: string,
    data: {
      chapter_num?: number;
      volume_no?: number;
      feedback_type?: string;
      rating?: number;
      tags?: string[];
      comment?: string;
    }
  ) =>
    fetchApi<NovelFeedback>(`/api/novels/${novelId}/feedback`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getObservability: (novelId: string) =>
    fetchApi<ObservabilityPayload>(`/api/novels/${novelId}/observability`),

  // Storyboards
  listStoryboardProjects: () =>
    fetchApi<StoryboardProject[]>("/api/storyboards"),

  createStoryboardProject: (data: StoryboardCreateData) =>
    fetchApi<StoryboardProject>("/api/storyboards", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getStoryboardStylePresets: () =>
    fetchApi<{ genre_styles: StoryboardStylePresetItem[]; director_styles: StoryboardStylePresetItem[] }>(
      "/api/storyboards/style-presets"
    ),

  getStoryboardStyleRecommendations: (novelId: string) =>
    fetchApi<{ novel_id: string; recommendations: StoryboardStyleRecommendationItem[] }>(
      "/api/storyboards/style-recommendations",
      {
        method: "POST",
        body: JSON.stringify({ novel_id: novelId }),
      }
    ),

  generateStoryboard: (projectId: number, novelVersionId: number) =>
    fetchApi<StoryboardGenerateResponse>(`/api/storyboards/${projectId}/generate`, {
      method: "POST",
      body: JSON.stringify({ novel_version_id: novelVersionId }),
    }),

  getStoryboardStatus: (projectId: number, taskId?: string) =>
    fetchApi<StoryboardTaskStatus>(
      `/api/storyboards/${projectId}/status${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`
    ),

  pauseStoryboard: (projectId: number, taskId?: string) =>
    fetchApi<{ ok: boolean; storyboard_project_id: number; task_id?: string; run_state?: string }>(
      `/api/storyboards/${projectId}/pause${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`,
      { method: "POST" }
    ),

  resumeStoryboard: (projectId: number, taskId?: string) =>
    fetchApi<{ ok: boolean; storyboard_project_id: number; task_id?: string; run_state?: string }>(
      `/api/storyboards/${projectId}/resume${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`,
      { method: "POST" }
    ),

  cancelStoryboard: (projectId: number, taskId?: string) =>
    fetchApi<{ ok: boolean; storyboard_project_id: number; task_id?: string; run_state?: string }>(
      `/api/storyboards/${projectId}/cancel${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ""}`,
      { method: "POST" }
    ),

  retryStoryboard: (projectId: number) =>
    fetchApi<{ ok: boolean; storyboard_project_id: number; task_id?: string; run_state?: string }>(
      `/api/storyboards/${projectId}/retry`,
      { method: "POST" }
    ),

  listStoryboardVersions: (projectId: number) =>
    fetchApi<StoryboardVersion[]>(`/api/storyboards/${projectId}/versions`),

  activateStoryboardVersion: (projectId: number, versionId: number) =>
    fetchApi<{ ok: boolean; storyboard_project_id: number }>(
      `/api/storyboards/${projectId}/versions/${versionId}/activate`,
      { method: "POST" }
    ),

  finalizeStoryboardVersion: (projectId: number, versionId: number) =>
    fetchApi<{ ok: boolean; storyboard_project_id: number }>(
      `/api/storyboards/${projectId}/versions/${versionId}/finalize`,
      { method: "POST" }
    ),

  listStoryboardShots: (projectId: number, versionId?: number, episodeNo?: number) => {
    const qs = new URLSearchParams();
    if (versionId !== undefined) qs.set("version_id", String(versionId));
    if (episodeNo !== undefined) qs.set("episode_no", String(episodeNo));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return fetchApi<StoryboardShot[]>(`/api/storyboards/${projectId}/shots${suffix}`);
  },

  updateStoryboardShot: (projectId: number, shotId: number, data: Partial<StoryboardShot>) =>
    fetchApi<StoryboardShot>(`/api/storyboards/${projectId}/shots/${shotId}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  listStoryboardCharacterPrompts: (projectId: number, versionId?: number, lane?: StoryboardLane) => {
    const qs = new URLSearchParams();
    if (versionId !== undefined) qs.set("version_id", String(versionId));
    if (lane) qs.set("lane", lane);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return fetchApi<StoryboardCharacterPrompt[]>(`/api/storyboards/${projectId}/characters${suffix}`);
  },

  regenerateStoryboardCharacterPrompts: (projectId: number, versionId?: number, lane?: StoryboardLane) => {
    const qs = new URLSearchParams();
    if (versionId !== undefined) qs.set("version_id", String(versionId));
    if (lane) qs.set("lane", lane);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return fetchApi<{
      ok: boolean;
      storyboard_project_id: number;
      storyboard_version_id: number;
      lane: StoryboardLane;
      generated_count: number;
      profiles_count: number;
      missing_identity_fields_count: number;
      failed_identity_characters: Array<Record<string, unknown>>;
    }>(`/api/storyboards/${projectId}/characters/generate${suffix}`, { method: "POST" });
  },

  optimizeStoryboardVersion: (projectId: number, versionId: number) =>
    fetchApi<{
      ok: boolean;
      storyboard_project_id: number;
      version_id: number;
      optimized_shots: number;
      quality_report_json: Record<string, unknown>;
    }>(`/api/storyboards/${projectId}/versions/${versionId}/optimize`, {
      method: "POST",
    }),

  getStoryboardDiff: (projectId: number, versionId: number, compareTo: number) =>
    fetchApi<{
      storyboard_project_id: number;
      version_id: number;
      compare_to: number;
      summary: Record<string, number>;
      episodes: Array<{ episode_no: number; added: number; removed: number; changed: number }>;
    }>(`/api/storyboards/${projectId}/versions/${versionId}/diff?compare_to=${encodeURIComponent(compareTo)}`),

  getStoryboardCsvUrl: (projectId: number, versionId: number) =>
    `${BASE}/api/storyboards/${projectId}/export/csv?version_id=${encodeURIComponent(versionId)}`,

  getStoryboardCharacterExportUrl: (projectId: number, versionId: number, lane?: StoryboardLane, format: "csv" | "json" = "csv") => {
    const qs = new URLSearchParams();
    qs.set("version_id", String(versionId));
    if (lane) qs.set("lane", lane);
    qs.set("format", format);
    return `${BASE}/api/storyboards/${projectId}/characters/export?${qs.toString()}`;
  },
};

export default api;
