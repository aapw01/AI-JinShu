"use client";

import { useEffect, useState } from "react";

import { api, NotificationItem } from "@/lib/api";

export default function NotificationsPage() {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    const run = async () => {
      try {
        const data = await api.getNotifications(60);
        if (!mounted) return;
        setItems(data);
      } finally {
        if (mounted) setLoading(false);
      }
    };
    run();
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <main className="min-h-screen bg-[#F7F5F2] px-6 py-8">
      <div className="mx-auto w-full max-w-[1120px] rounded-2xl border border-[#E8E2DA] bg-white p-6">
        <h1 className="text-[26px] font-semibold text-[#1F1B18]">通知中心</h1>
        <p className="mt-1 text-sm text-[#8B8379]">生成完成、失败和重写状态会显示在这里</p>
        <div className="mt-5 space-y-3">
          {items.map((item) => (
            <article key={item.id} className="rounded-xl border border-[#EEE7DE] px-4 py-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold text-[#201C19]">{item.title}</h2>
                  <p className="mt-1 text-sm text-[#6F665D]">{item.message || "状态已更新"}</p>
                </div>
                <span className="text-xs text-[#A2978C]">{item.created_at.slice(0, 16).replace("T", " ")}</span>
              </div>
            </article>
          ))}
          {!items.length && !loading ? <p className="text-sm text-[#8B8379]">暂无通知</p> : null}
          {loading ? <p className="text-sm text-[#8B8379]">加载中...</p> : null}
        </div>
      </div>
    </main>
  );
}

