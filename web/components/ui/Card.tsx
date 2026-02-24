"use client";

import { HTMLAttributes, forwardRef } from "react";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  hover?: boolean;
}

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ className = "", hover = false, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={`
          bg-white/76 backdrop-blur-xl border border-white/85 rounded-[12px] shadow-[0_2px_10px_rgba(0,0,0,0.06)]
          ${hover ? "hover:-translate-y-[2px] hover:shadow-[0_12px_30px_rgba(0,0,0,0.09)] transition-all cursor-pointer" : ""}
          ${className}
        `}
        style={{ transitionTimingFunction: "cubic-bezier(0.25, 0.1, 0.25, 1)" }}
        {...props}
      >
        {children}
      </div>
    );
  }
);

Card.displayName = "Card";

export const CardHeader = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className = "", ...props }, ref) => (
    <div ref={ref} className={`p-6 pb-4 ${className}`} {...props} />
  )
);
CardHeader.displayName = "CardHeader";

export const CardContent = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className = "", ...props }, ref) => (
    <div ref={ref} className={`p-6 pt-0 ${className}`} {...props} />
  )
);
CardContent.displayName = "CardContent";

export const CardFooter = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className = "", ...props }, ref) => (
    <div ref={ref} className={`p-6 pt-4 border-t border-[rgba(60,60,67,0.12)] ${className}`} {...props} />
  )
);
CardFooter.displayName = "CardFooter";
