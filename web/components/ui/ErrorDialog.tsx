"use client";

import { Modal } from "./Modal";
import { Button } from "./Button";

interface ErrorDialogProps {
  open: boolean;
  title?: string;
  message: string;
  onClose: () => void;
}

export function ErrorDialog({
  open,
  title = "操作失败",
  message,
  onClose,
}: ErrorDialogProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <Button onClick={onClose} className="min-w-[88px]">
          知道了
        </Button>
      }
    >
      <p className="text-sm leading-6 text-[#4B4540] whitespace-pre-wrap">
        {message}
      </p>
    </Modal>
  );
}

