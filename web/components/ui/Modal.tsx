"use client";

import { useEffect, useCallback, ReactNode } from "react";
import { Button } from "./Button";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  footer?: ReactNode;
  maxWidthClassName?: string;
  panelClassName?: string;
  bodyClassName?: string;
  footerClassName?: string;
}

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  maxWidthClassName = "max-w-md",
  panelClassName = "",
  bodyClassName = "",
  footerClassName = "",
}: ModalProps) {
  const handleEscape = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose]
  );

  useEffect(() => {
    if (open) {
      document.addEventListener("keydown", handleEscape);
      document.body.style.overflow = "hidden";
    }
    return () => {
      document.removeEventListener("keydown", handleEscape);
      document.body.style.overflow = "";
    };
  }, [open, handleEscape]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/15 backdrop-blur-sm" onClick={onClose} />
      <div
        className={`relative w-full mx-4 bg-white/90 border border-white rounded-[12px] shadow-[0_16px_40px_rgba(0,0,0,0.14)] animate-fade-in ${maxWidthClassName} ${panelClassName}`}
      >
        {title && (
          <div className="flex items-center justify-between p-6 border-b border-[rgba(60,60,67,0.12)]">
            <h3 className="text-lg font-semibold text-[#1D1D1F]">{title}</h3>
            <button
              onClick={onClose}
              className="text-[#8E8E93] hover:text-[#1D1D1F] transition-colors"
              aria-label="Close"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        )}
        <div className={`p-6 ${bodyClassName}`}>{children}</div>
        {footer && <div className={`p-6 pt-0 flex justify-end gap-3 ${footerClassName}`}>{footer}</div>}
      </div>
    </div>
  );
}

interface ConfirmModalProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  confirmVariant?: "primary" | "secondary" | "ghost" | "destructive";
  loading?: boolean;
}

export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmText = "确认",
  cancelText = "取消",
  confirmVariant = "destructive",
  loading,
}: ConfirmModalProps) {
  const confirmClassName =
    confirmVariant === "destructive"
      ? "min-w-[104px] h-9 px-4 shadow-none"
      : "min-w-[104px] h-9 px-4";

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      bodyClassName="pb-5"
      footerClassName="items-center gap-2.5"
      footer={
        <>
          <Button
            variant="ghost"
            size="sm"
            className="h-9 min-w-[72px] px-3.5 text-[#7E756D] hover:bg-[#F5F1EC]"
            onClick={onClose}
            disabled={loading}
          >
            {cancelText}
          </Button>
          <Button
            variant={confirmVariant}
            size="sm"
            className={confirmClassName}
            onClick={onConfirm}
            loading={loading}
          >
            {confirmText}
          </Button>
        </>
      }
    >
      <p className="text-[15px] leading-7 text-[#4A433D]">{message}</p>
    </Modal>
  );
}
