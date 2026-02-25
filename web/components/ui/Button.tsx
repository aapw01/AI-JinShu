"use client";

import { forwardRef, ButtonHTMLAttributes } from "react";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "ghost" | "destructive";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = "", variant = "primary", size = "md", loading, disabled, children, ...props }, ref) => {
    const baseStyles =
      "inline-flex items-center justify-center font-medium transition-all disabled:opacity-55 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#C8211B]/30";

    const variants = {
      primary:
        "bg-[#C8211B] text-white hover:bg-[#AD1B16] shadow-[0_6px_20px_rgba(200,33,27,0.24)] hover:shadow-[0_10px_24px_rgba(173,27,22,0.26)]",
      secondary: "bg-white text-[#1F1B18] border border-[#DDD8D3] hover:bg-[#F6F3EF] shadow-[0_2px_10px_rgba(0,0,0,0.04)]",
      ghost: "text-[#7E756D] hover:text-[#1F1B18] hover:bg-[#F6F3EF]",
      destructive: "bg-[#FFE9E8] text-[#C4372D] border border-[#FFD4D2] hover:bg-[#FFDCDC]",
    };

    const sizes = {
      sm: "px-3 py-1.5 text-sm rounded-[8px]",
      md: "px-5 py-2.5 rounded-[8px]",
      lg: "px-8 py-3 text-lg rounded-[8px]",
    };

    return (
      <button
        ref={ref}
        className={`${baseStyles} ${variants[variant]} ${sizes[size]} ${className}`}
        style={{ transitionTimingFunction: "cubic-bezier(0.25, 0.1, 0.25, 1)" }}
        disabled={disabled || loading}
        {...props}
      >
        {loading && (
          <svg className="animate-spin -ml-1 mr-2 h-4 w-4" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
          </svg>
        )}
        {children}
      </button>
    );
  }
);

Button.displayName = "Button";
