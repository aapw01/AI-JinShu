"use client";

import { useQuery } from "@tanstack/react-query";
import {
  api,
  Chapter,
  ChapterProgress,
  GenerationTaskItem,
  Novel,
  NovelVersion,
  PresetCategory,
} from "@/lib/api";
import { isActiveGenerationTaskStatus } from "@/lib/display";

type LabelMap = Record<string, string>;

function buildPresetLabelMap(items?: PresetCategory[string]): LabelMap {
  const out: LabelMap = {};
  if (!Array.isArray(items)) return out;
  for (const item of items) {
    if (!item?.id || !item?.label) continue;
    out[item.id] = item.label;
  }
  return out;
}

function pickDisplayGenerationTask(tasks: GenerationTaskItem[]): GenerationTaskItem | null {
  return tasks.find((task) => isActiveGenerationTaskStatus(task.status)) || tasks[0] || null;
}

export interface NovelPageBaseData {
  novel: Novel;
  versions: NovelVersion[];
  defaultVersion: NovelVersion | null;
  activeGenerationTask: GenerationTaskItem | null;
  genreLabelMap: LabelMap;
  styleLabelMap: LabelMap;
}

export interface NovelVersionData {
  chapters: Chapter[];
  chapterProgress: ChapterProgress[];
}

export function useNovelPageBaseData(novelId: string) {
  return useQuery<NovelPageBaseData>({
    queryKey: ["novel-page", "base", novelId],
    enabled: Boolean(novelId),
    queryFn: async () => {
      const [novel, presets, generationTasks, versions] = await Promise.all([
        api.getNovel(novelId),
        api.getPresets().catch(() => null as PresetCategory | null),
        api.listGenerationTasks(novelId, 10).catch(() => [] as GenerationTaskItem[]),
        api.getVersions(novelId).catch(() => [] as NovelVersion[]),
      ]);
      return {
        novel,
        versions,
        defaultVersion: versions.find((item) => item.is_default) || versions[0] || null,
        activeGenerationTask: pickDisplayGenerationTask(generationTasks),
        genreLabelMap: buildPresetLabelMap(presets?.genres),
        styleLabelMap: buildPresetLabelMap(presets?.styles),
      };
    },
    refetchInterval: 5000,
    placeholderData: (previous) => previous,
  });
}

export function useNovelVersionData(novelId: string, versionId: number | null) {
  return useQuery<NovelVersionData>({
    queryKey: ["novel-page", "version", novelId, versionId],
    enabled: Boolean(novelId) && Boolean(versionId),
    queryFn: async () => {
      const resolvedVersionId = Number(versionId);
      const [chapterProgress, chapters] = await Promise.all([
        api.getChapterProgress(novelId, resolvedVersionId),
        api.getChapters(novelId, resolvedVersionId),
      ]);
      return {
        chapters,
        chapterProgress,
      };
    },
    placeholderData: (previous) => previous,
  });
}
