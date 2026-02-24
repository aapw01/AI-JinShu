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
    <header className="sticky top-0 z-40 border-b border-[rgba(60,60,67,0.16)] bg-[rgba(245,245,247,0.82)] backdrop-blur-xl">
      <div className={`${maxWidthClassName} mx-auto px-4 py-4 flex items-center justify-between`}>
        <div className="flex items-center gap-3">
          {backHref && (
            <Link href={backHref} className="text-[#6E6E73] hover:text-[#1D1D1F] transition-colors">
              {icon ?? <ChevronLeft className="w-5 h-5" />}
            </Link>
          )}
          <div>
            <h1 className="text-xl font-semibold text-[#1D1D1F]">{title}</h1>
            {subtitle ? <p className="text-sm text-[#6E6E73]">{subtitle}</p> : null}
          </div>
        </div>
        {actions ? <div className="flex items-center gap-3">{actions}</div> : null}
      </div>
    </header>
  );
}
