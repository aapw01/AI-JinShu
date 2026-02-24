"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight, BookOpen, Download, Home as HomeIcon, Sparkles } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

export default function Home() {
  return (
    <main className="min-h-screen">
      <div className="max-w-6xl mx-auto px-4 py-10 md:py-14">
        <motion.div
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: [0.25, 0.1, 0.25, 1] }}
          className="glass-card p-8 md:p-12 mb-8"
        >
          <div className="inline-flex items-center gap-2 rounded-full px-3 py-1 mb-4 text-xs bg-[#EAF3FF] text-[#0062CC] border border-[#CFE2FF]">
            <Sparkles className="w-3.5 h-3.5" />
            Apple-inspired 创作工作台
          </div>
          <h1 className="text-4xl md:text-5xl font-semibold tracking-tight text-[#1D1D1F]">AI-JinShu</h1>
          <p className="mt-4 text-[#6E6E73] text-lg max-w-2xl">
            用更克制、专业、可追踪的方式生成长篇小说。从大纲到章节，再到导出与复审，全程可视化。
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/create">
              <Button size="lg">
                开始创作
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </Link>
            <Link href="/novels">
              <Button size="lg" variant="secondary">
                <BookOpen className="mr-2 h-4 w-4" />
                我的作品
              </Button>
            </Link>
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.55, ease: [0.25, 0.1, 0.25, 1], delay: 0.08 }}
          className="grid grid-cols-1 md:grid-cols-3 gap-4"
        >
          <FeatureCard icon={<HomeIcon className="w-5 h-5" />} title="清晰流程" description="先全书规划，再分章执行，减少跑偏与遗忘。" />
          <FeatureCard icon={<Download className="w-5 h-5" />} title="完整交付" description="支持 TXT / Markdown / ZIP 导出与终审报告。" />
          <FeatureCard icon={<Sparkles className="w-5 h-5" />} title="多语言创作" description="9 种语言母语风格，质量评分可回看。" />
        </motion.div>
      </div>
    </main>
  );
}

function FeatureCard({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <Card className="p-5">
      <div className="w-10 h-10 rounded-[10px] bg-[#F2F2F4] border border-[rgba(60,60,67,0.14)] flex items-center justify-center text-[#007AFF] mb-3">
        {icon}
      </div>
      <h3 className="text-[#1D1D1F] font-semibold">{title}</h3>
      <p className="text-sm text-[#6E6E73] mt-1">{description}</p>
    </Card>
  );
}
