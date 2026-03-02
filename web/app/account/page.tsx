"use client";

import { useEffect, useState } from "react";

import { api, AccountQuota, UsageLedgerItem } from "@/lib/api";
import { formatPlanKey, formatUsageSource } from "@/lib/display";

export default function AccountPage() {
  const [quota, setQuota] = useState<AccountQuota | null>(null);
  const [ledger, setLedger] = useState<UsageLedgerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const run = async () => {
      try {
        const [q, l] = await Promise.all([api.getQuota(), api.getUsageLedger(20)]);
        if (!mounted) return;
        setQuota(q);
        setLedger(l);
      } catch {
        if (mounted) setError("加载失败，请稍后重试");
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
    <main className="min-h-screen bg-[#F4F3F1] px-6 py-8">
      <div className="mx-auto w-full max-w-[1280px] space-y-6">
        <section className="rounded-[14px] border border-[#DDD8D3] bg-white p-6">
          <h1 className="text-2xl font-semibold text-[#1F1B18]">账户中心</h1>
          <p className="mt-1 text-sm text-[#8E8379]">查看套餐额度、使用进度和成本明细</p>
          {loading ? (
            <p className="mt-4 text-sm text-[#8E8379]">加载中...</p>
          ) : error ? (
            <p className="mt-4 text-sm text-[#C4372D]">{error}</p>
          ) : quota ? (
            <div className="mt-5 grid gap-3 md:grid-cols-4">
              <div className="rounded-[12px] border border-[#DDD8D3] p-3">
                <p className="text-xs text-[#8E8379]">套餐</p>
                <p className="mt-1 text-lg font-semibold text-[#1F1B18]">{formatPlanKey(quota.plan_key)}</p>
              </div>
              <div className="rounded-[12px] border border-[#DDD8D3] p-3">
                <p className="text-xs text-[#8E8379]">并发上限</p>
                <p className="mt-1 text-lg font-semibold text-[#1F1B18]">{quota.max_concurrent_tasks}</p>
              </div>
              <div className="rounded-[12px] border border-[#DDD8D3] p-3">
                <p className="text-xs text-[#8E8379]">章节额度</p>
                <p className="mt-1 text-lg font-semibold text-[#1F1B18]">
                  {quota.used_chapters} / {quota.monthly_chapter_limit}
                </p>
              </div>
              <div className="rounded-[12px] border border-[#DDD8D3] p-3">
                <p className="text-xs text-[#8E8379]">Token额度</p>
                <p className="mt-1 text-lg font-semibold text-[#1F1B18]">
                  {quota.used_tokens} / {quota.monthly_token_limit}
                </p>
              </div>
            </div>
          ) : null}
        </section>

        <section className="rounded-[14px] border border-[#DDD8D3] bg-white p-6">
          <h2 className="text-xl font-semibold text-[#1F1B18]">账本明细</h2>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-[#DDD8D3] text-[#8E8379]">
                  <th className="pb-2">任务ID</th>
                  <th className="pb-2">来源</th>
                  <th className="pb-2">输入Token</th>
                  <th className="pb-2">输出Token</th>
                  <th className="pb-2">章节</th>
                  <th className="pb-2">成本</th>
                </tr>
              </thead>
              <tbody>
                {ledger.map((item) => (
                  <tr key={`${item.task_id}-${item.created_at}`} className="border-b border-[#F8F4EF]">
                    <td className="py-2 font-mono text-xs">{item.task_id}</td>
                    <td className="py-2">{formatUsageSource(item.source)}</td>
                    <td className="py-2">{item.input_tokens}</td>
                    <td className="py-2">{item.output_tokens}</td>
                    <td className="py-2">{item.chapters_generated}</td>
                    <td className="py-2">${(item.estimated_cost ?? 0).toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!ledger.length && !loading ? <p className="pt-4 text-sm text-[#8E8379]">暂无账本记录</p> : null}
          </div>
        </section>
      </div>
    </main>
  );
}
