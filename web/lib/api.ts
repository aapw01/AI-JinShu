const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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

export interface ChapterProgress {
  chapter_num: number;
  title?: string;
  status: "pending" | "generating" | "completed";
}

export interface GenerationStatus {
  status: string;
  progress: number;
  current_phase?: string;
  step?: string;
  current_chapter?: number;
  total_chapters?: number;
  token_usage_input?: number;
  token_usage_output?: number;
  estimated_cost?: number;
  message?: string;
  error?: string;
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
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

// Fetch wrapper with error handling
async function fetchApi<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text || `HTTP ${res.status}`);
  }

  // Handle empty responses
  const text = await res.text();
  if (!text) return {} as T;

  return JSON.parse(text) as T;
}

// API methods
export const api = {
  // Novels
  listNovels: () => fetchApi<Novel[]>("/api/novels"),

  createNovel: (data: CreateNovelData) =>
    fetchApi<{ id: string }>("/api/novels", {
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
  getChapters: (novelId: string) =>
    fetchApi<Chapter[]>(`/api/novels/${novelId}/chapters`),

  getChapter: (novelId: string, chapterNum: number) =>
    fetchApi<Chapter>(`/api/novels/${novelId}/chapters/${chapterNum}`),

  getChapterProgress: (novelId: string) =>
    fetchApi<ChapterProgress[]>(`/api/novels/${novelId}/chapter-progress`),

  updateChapter: (novelId: string, chapterNum: number, data: { title?: string; content?: string }) =>
    fetchApi<Chapter>(`/api/novels/${novelId}/chapters/${chapterNum}`, {
      method: "PUT",
      body: JSON.stringify(data),
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

  confirmOutline: (novelId: string, taskId: string) =>
    fetchApi<{ ok: boolean; message: string }>(`/api/novels/${novelId}/generation/${taskId}/confirm-outline`, {
      method: "POST",
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
};

export default api;
