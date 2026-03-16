"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";

import { api, getErrorMessage } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { ErrorDialog } from "@/components/ui";

function ForgotPasswordPageContent() {
  const search = useSearchParams();
  const token = search.get("token") || "";
  const isResetMode = Boolean(token);
  const [email, setEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [errorDialogOpen, setErrorDialogOpen] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [notice, setNotice] = useState("");

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMessage("");
    setErrorDialogOpen(false);
    setNotice("");
    setLoading(true);
    try {
      if (isResetMode) {
        await api.resetPassword(token, newPassword);
        setNotice("密码已重置，3秒后跳转登录页...");
        setTimeout(() => { window.location.href = "/auth/login"; }, 3000);
      } else {
        await api.forgotPassword(email);
        setNotice("若邮箱存在，将收到重置邮件。");
      }
    } catch (err) {
      setErrorMessage(getErrorMessage(err, "操作失败"));
      setErrorDialogOpen(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_0%_0%,#FBEDEA_0%,#F5F1ED_35%,#F2F0EC_100%)]">
      <div className="max-w-md mx-auto px-4 py-24">
          <div className="rounded-[14px] border border-[#DDD8D3] bg-white/90 p-6 shadow-[0_18px_40px_rgba(31,27,24,0.08)]">
          <h1 className="text-2xl font-semibold text-[#1F1B18]">{isResetMode ? "重置密码" : "找回密码"}</h1>
          <form className="mt-6 space-y-4" onSubmit={onSubmit}>
            {isResetMode ? (
              <div>
                <label className="block text-sm text-[#5E5650] mb-1">新密码</label>
                <input
                  className="w-full h-10 rounded-[8px] border border-[#DDD8D3] px-3 text-sm outline-none focus:border-[#C8211B] focus:ring-2 focus:ring-[#C8211B]/10 transition-colors"
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                />
              </div>
            ) : (
              <div>
                <label className="block text-sm text-[#5E5650] mb-1">邮箱</label>
                <input
                  className="w-full h-10 rounded-[8px] border border-[#DDD8D3] px-3 text-sm outline-none focus:border-[#C8211B] focus:ring-2 focus:ring-[#C8211B]/10 transition-colors"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </div>
            )}
            {notice ? <p className="text-xs text-[#18864B]">{notice}</p> : null}
            <Button className="w-full" loading={loading} type="submit">
              {isResetMode ? "提交新密码" : "发送重置邮件"}
            </Button>
          </form>
          <p className="mt-4 text-sm text-[#7E756D]">
            <Link className="text-[#C8211B]" href="/auth/login">
              返回登录
            </Link>
          </p>
        </div>
      </div>
      <ErrorDialog
        open={errorDialogOpen}
        onClose={() => setErrorDialogOpen(false)}
        title={isResetMode ? "重置失败" : "发送失败"}
        message={errorMessage || "请稍后重试"}
      />
    </main>
  );
}

export default function ForgotPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ForgotPasswordPageContent />
    </Suspense>
  );
}
