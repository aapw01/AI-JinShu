"use client";

import { HTMLAttributes, forwardRef } from "react";

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: "default" | "success" | "warning" | "error" | "info";
}

export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className = "", variant = "default", children, ...props }, ref) => {
    const variants = {
      default: "bg-[#F2F2F4] text-[#6E6E73] border border-[rgba(60,60,67,0.14)]",
      success: "bg-[#E9F9EF] text-[#18864B] border border-[#CDEFD8]",
      warning: "bg-[#FFF7E7] text-[#A96700] border border-[#FFE6B3]",
      error: "bg-[#FFECEB] text-[#C4372D] border border-[#FFD4D2]",
      info: "bg-[#EAF3FF] text-[#0062CC] border border-[#CFE2FF]",
    };

    return (
      <span
        ref={ref}
        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${variants[variant]} ${className}`}
        {...props}
      >
        {children}
      </span>
    );
  }
);

Badge.displayName = "Badge";
