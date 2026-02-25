"use client";

import { Card } from "./Card";

interface StatsCardProps {
  label: string;
  value: string;
  hint?: string;
}

export function StatsCard({ label, value, hint }: StatsCardProps) {
  return (
    <Card className="p-4">
      <p className="text-xs text-[#7E756D]">{label}</p>
      <p className="text-lg font-semibold text-[#1F1B18] mt-1">{value}</p>
      {hint ? <p className="text-[11px] text-[#8E8E93] mt-1">{hint}</p> : null}
    </Card>
  );
}
