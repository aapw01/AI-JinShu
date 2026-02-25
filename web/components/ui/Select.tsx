"use client";

import { forwardRef, SelectHTMLAttributes } from "react";

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  options: { value: string; label: string }[];
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ className = "", label, error, options, id, ...props }, ref) => {
    const selectId = id || label?.toLowerCase().replace(/\s+/g, "-");

    return (
      <div className="space-y-2">
        {label && (
          <label htmlFor={selectId} className="block text-sm font-medium text-[#3A3A3C]">
            {label}
          </label>
        )}
        <div className="relative">
          <select
            ref={ref}
            id={selectId}
            className={`
              w-full bg-white border border-[#DDD8D3] rounded-[8px] px-4 py-3 text-[#1F1B18]
              focus:outline-none focus:ring-2 focus:ring-[#C8211B]/15 focus:border-[#C8211B]
              transition-all duration-200 appearance-none cursor-pointer
              ${error ? "border-[#C4372D]/50 focus:ring-[#C4372D]/30" : ""}
              ${className}
            `}
            {...props}
          >
            {options.map((opt) => (
              <option key={opt.value} value={opt.value} className="bg-white text-[#1D1D1F]">
                {opt.label}
              </option>
            ))}
          </select>
          <div className="absolute inset-y-0 right-0 flex items-center pr-3 pointer-events-none">
            <svg className="w-5 h-5 text-[#8E8E93]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>
        {error && <p className="text-sm text-[#C4372D]">{error}</p>}
      </div>
    );
  }
);

Select.displayName = "Select";
