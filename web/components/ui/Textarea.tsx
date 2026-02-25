"use client";

import { forwardRef, TextareaHTMLAttributes } from "react";

interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className = "", label, error, id, ...props }, ref) => {
    const textareaId = id || label?.toLowerCase().replace(/\s+/g, "-");

    return (
      <div className="space-y-2">
        {label && (
          <label htmlFor={textareaId} className="block text-sm font-medium text-[#3A3A3C]">
            {label}
          </label>
        )}
        <textarea
          ref={ref}
          id={textareaId}
          className={`
            w-full bg-white border border-[#DDD8D3] rounded-[8px] px-4 py-3 text-[#1F1B18]
            placeholder:text-[#8E8E93] focus:outline-none focus:ring-2 focus:ring-[#C8211B]/15
            focus:border-[#C8211B] transition-all duration-200 resize-none min-h-[120px]
            ${error ? "border-[#C4372D]/50 focus:ring-[#C4372D]/30" : ""}
            ${className}
          `}
          {...props}
        />
        {error && <p className="text-sm text-[#C4372D]">{error}</p>}
      </div>
    );
  }
);

Textarea.displayName = "Textarea";
