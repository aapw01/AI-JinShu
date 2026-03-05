"use client";

import Link from "next/link";
import { ChevronLeft } from "lucide-react";

interface TopBarProps {
  title: string;
  subtitle?: string;
  backHref?: string;
  actions?: React.ReactNode;
  icon?: React.ReactNode;
  maxWidthClassName?: string;
}

export function TopBar({
  title,
  subtitle,
  backHref,
  actions,
  icon,
  maxWidthClassName = "max-w-6xl",
}: TopBarProps) {
  return (
    <header className="border-b border-[#E4DFDA]">
      <div className={`${maxWidthClassName} mx-auto px-4 py-3 flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between`}>
        <div className="flex min-w-0 items-center gap-3">
          {backHref && (
            <Link href={backHref} className="text-[#7E756D] hover:text-[#1F1B18] transition-colors">
              {icon ?? <ChevronLeft className="w-5 h-5" />}
            </Link>
          )}
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-[#1F1B18] leading-tight">{title}</h1>
            {subtitle ? <p className="text-xs text-[#7E756D] mt-0.5">{subtitle}</p> : null}
          </div>
        </div>
        {actions ? <div className="flex flex-wrap items-center justify-end gap-2 w-full lg:w-auto">{actions}</div> : null}
      </div>
    </header>
  );
}
