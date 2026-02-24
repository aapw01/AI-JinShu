"use client";

interface SectionTitleProps {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}

export function SectionTitle({ title, subtitle, right }: SectionTitleProps) {
  return (
    <div className="flex items-start justify-between gap-3 mb-4">
      <div>
        <h2 className="text-lg font-semibold text-[#1D1D1F]">{title}</h2>
        {subtitle ? <p className="text-sm text-[#6E6E73] mt-1">{subtitle}</p> : null}
      </div>
      {right}
    </div>
  );
}
