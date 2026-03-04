"use client";

import { Check, ChevronDown } from "lucide-react";
import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";

type SelectOption = {
  value: string;
  label: string;
};

interface SelectProps {
  id?: string;
  name?: string;
  className?: string;
  disabled?: boolean;
  value?: string;
  label?: string;
  error?: string;
  options: SelectOption[];
  placeholder?: string;
  onChange?: (event: ChangeEvent<HTMLSelectElement>) => void;
  onValueChange?: (value: string) => void;
  onBlur?: () => void;
  onFocus?: () => void;
}

export function Select({
  id,
  name,
  className = "",
  disabled = false,
  value,
  label,
  error,
  options,
  placeholder = "请选择",
  onChange,
  onValueChange,
  onBlur,
  onFocus,
}: SelectProps) {
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const selectId = id || label?.toLowerCase().replace(/\s+/g, "-");
  const [open, setOpen] = useState(false);
  const currentValue = String(value ?? "");

  const activeOption = useMemo(
    () => options.find((opt) => String(opt.value) === currentValue) || null,
    [options, currentValue]
  );

  useEffect(() => {
    const onDocClick = (event: MouseEvent) => {
      if (!wrapperRef.current) return;
      if (!wrapperRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const emitChange = (nextValue: string) => {
    onValueChange?.(nextValue);
    if (onChange) {
      const event = {
        target: { value: nextValue },
        currentTarget: { value: nextValue },
      } as ChangeEvent<HTMLSelectElement>;
      onChange(event);
    }
  };

  const triggerLabel = activeOption?.label || placeholder;

  return (
    <div className={label ? "space-y-2" : "space-y-0"}>
      {label ? (
        <label htmlFor={selectId} className="block text-sm font-medium text-[#3A3A3C]">
          {label}
        </label>
      ) : null}
      <div className="relative" ref={wrapperRef}>
        <input type="hidden" name={name} value={currentValue} />
        <button
          id={selectId}
          type="button"
          disabled={disabled}
          onBlur={onBlur}
          onFocus={onFocus}
          className={`
            w-full inline-flex items-center justify-between gap-3 rounded-[10px] border border-[#DDD8D3] bg-white px-4 py-3 text-left
            transition-all duration-200
            focus:outline-none focus:ring-2 focus:ring-[#C8211B]/15 focus:border-[#C8211B]
            ${disabled ? "cursor-not-allowed bg-[#F4F2EF] text-[#A29A91]" : "cursor-pointer"}
            ${error ? "border-[#C4372D]/50 focus:ring-[#C4372D]/30" : ""}
            ${className}
          `}
          onClick={() => {
            if (!disabled) setOpen((v) => !v);
          }}
          onKeyDown={(e) => {
            if (disabled) return;
            if (e.key === "Escape") {
              setOpen(false);
              return;
            }
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              setOpen((v) => !v);
              return;
            }
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
            }
          }}
        >
          <span className={`truncate ${activeOption ? "text-[#1F1B18]" : "text-[#9D948B]"}`}>{triggerLabel}</span>
          <ChevronDown className={`w-4 h-4 text-[#8E8E93] transition-transform ${open ? "rotate-180" : ""}`} />
        </button>

        {open && !disabled ? (
          <div className="absolute z-[80] mt-1 w-full rounded-[10px] border border-[#E7DFD7] bg-white p-1 shadow-[0_16px_40px_rgba(31,27,24,0.14)]">
            <div className="max-h-64 overflow-y-auto">
              {options.map((opt) => {
                const isActive = String(opt.value) === currentValue;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    className={`inline-flex h-9 w-full items-center justify-between rounded-[8px] px-3 text-left text-sm ${
                      isActive ? "bg-[#F8ECEA] text-[#A52A25]" : "text-[#3A3A3C] hover:bg-[#F6F2EE]"
                    }`}
                    onClick={() => {
                      emitChange(String(opt.value));
                      setOpen(false);
                    }}
                  >
                    <span className="truncate">{opt.label}</span>
                    {isActive ? <Check className="w-4 h-4" /> : null}
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}
      </div>
      {error ? <p className="text-sm text-[#C4372D]">{error}</p> : null}
    </div>
  );
}
