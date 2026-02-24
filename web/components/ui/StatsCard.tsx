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
      <p className="text-xs text-[#6E6E73]">{label}</p>
      <p className="text-xl font-semibold text-[#1D1D1F] mt-1">{value}</p>
      {hint ? <p className="text-xs text-[#8E8E93] mt-1">{hint}</p> : null}
    </Card>
  );
}
