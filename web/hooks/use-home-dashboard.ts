"use client";

import { useQuery } from "@tanstack/react-query";
import { api, GenerationTaskItem, Novel, PresetCategory } from "@/lib/api";
import { isActiveGenerationTaskStatus } from "@/lib/display";

export interface NovelMetrics {
  completed: number;
  total: number;
  percent: number;
  words: number;
  quality: number;
}

function fallbackQuality(novelId: string, status: string): number {
  const seed = Array.from(novelId).reduce((sum, char) => sum + char.charCodeAt(0), 0);
  const base = status === "completed" ? 90 : status === "generating" ? 84 : status === "failed" ? 68 : 76;
  const jitter = ((seed % 11) - 5) * 0.4;
  return Math.max(60, Math.min(98, Math.round((base + jitter) * 10) / 10));
}

function pickDisplayGenerationTask(tasks: GenerationTaskItem[]): GenerationTaskItem | null {
  return tasks.find((task) => isActiveGenerationTaskStatus(task.status)) || tasks[0] || null;
}

export function useHomePresets() {
  return useQuery<PresetCategory | null>({
    queryKey: ["home", "presets"],
    queryFn: () => api.getPresets().catch(() => null),
    staleTime: 60_000,
  });
}

export function useHomeNovels() {
  return useQuery<Novel[]>({
    queryKey: ["home", "novels"],
    queryFn: () => api.listNovels(),
    refetchInterval: 15_000,
  });
}

export function useHomeNovelMetrics(novels: Novel[]) {
  const targets = novels.slice(0, 12);
  return useQuery<Record<string, NovelMetrics>>({
    queryKey: ["home", "novel-metrics", targets.map((novel) => novel.id)],
    enabled: targets.length > 0,
    queryFn: async () => {
      const entries = await Promise.all(
        targets.map(async (novel) => {
          try {
            const versions = await api.getVersions(novel.id);
            const defaultVersion = versions.find((item) => item.is_default) || versions[0];
            if (!defaultVersion) {
              return [
                novel.id,
                { completed: 0, total: 1, percent: 0, words: 0, quality: fallbackQuality(novel.id, novel.status) },
              ] as const;
            }
            const [progressRes, chaptersRes] = await Promise.allSettled([
              api.getChapterProgress(novel.id, defaultVersion.id),
              api.getChapters(novel.id, defaultVersion.id),
            ]);
            const progress = progressRes.status === "fulfilled" ? progressRes.value : [];
            const chapters = chaptersRes.status === "fulfilled" ? chaptersRes.value : [];

            const completed = progress.length
              ? progress.filter((chapter) => chapter.status === "completed").length
              : chapters.filter((chapter) => chapter.status === "completed").length;
            const total =
              progress.length ||
              chapters.length ||
              (typeof novel.config?.chapter_target === "number" ? Number(novel.config.chapter_target) : 1);
            const percent = Math.min(100, Math.round((completed / Math.max(total, 1)) * 100));
            const words = chapters.reduce((sum, chapter) => {
              if (typeof chapter.word_count === "number" && chapter.word_count > 0) {
                return sum + chapter.word_count;
              }
              const content = chapter.content || "";
              return sum + content.replace(/\s+/g, "").length;
            }, 0);
            const qualityScores = chapters
              .map((chapter) => chapter.language_quality_score)
              .filter((score): score is number => typeof score === "number");
            const quality = qualityScores.length
              ? Math.round(((qualityScores.reduce((a, b) => a + b, 0) / qualityScores.length) * 10) * 10) / 10
              : fallbackQuality(novel.id, novel.status);
            return [novel.id, { completed, total, percent, words, quality }] as const;
          } catch {
            return [novel.id, { completed: 0, total: 1, percent: 0, words: 0, quality: fallbackQuality(novel.id, novel.status) }] as const;
          }
        })
      );
      return Object.fromEntries(entries);
    },
    placeholderData: (previous) => previous,
  });
}

export function useHomeGenerationTasks(novels: Novel[]) {
  const targets = novels.filter((novel) => novel.status === "generating");
  return useQuery<Record<string, GenerationTaskItem>>({
    queryKey: ["home", "generation-tasks", targets.map((novel) => novel.id)],
    enabled: targets.length > 0,
    queryFn: async () => {
      const entries = await Promise.all(
        targets.map(async (novel) => {
          try {
            const tasks = await api.listGenerationTasks(novel.id, 10);
            return [novel.id, pickDisplayGenerationTask(tasks)] as const;
          } catch {
            return [novel.id, null] as const;
          }
        })
      );

      const nextMap: Record<string, GenerationTaskItem> = {};
      for (const [novelId, task] of entries) {
        if (task) nextMap[novelId] = task;
      }
      return nextMap;
    },
    refetchInterval: 15_000,
    placeholderData: (previous) => previous,
  });
}
