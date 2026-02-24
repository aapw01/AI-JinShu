"use client";

import { Card } from "./Card";

interface EmptyStateProps {
  icon: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <Card className="p-10 text-center">
      <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-[#F2F2F4] border border-[rgba(60,60,67,0.14)] flex items-center justify-center text-[#8E8E93]">
        {icon}
      </div>
      <h3 className="text-lg font-semibold text-[#1D1D1F]">{title}</h3>
      {description ? <p className="text-sm text-[#6E6E73] mt-2 mb-5">{description}</p> : null}
      {action}
    </Card>
  );
}
