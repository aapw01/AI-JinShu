"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { ErrorDialog } from "@/components/ui/ErrorDialog";

function LoginPageContent() {
  const router = useRouter();
  const search = useSearchParams();
  const next = search.get("next") || "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [errorDialogOpen, setErrorDialogOpen] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [pendingVerification, setPendingVerification] = useState(false);
  const [resendLoading, setResendLoading] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMessage("");
    setErrorDialogOpen(false);
    setNotice("");
    setPendingVerification(false);
    setLoading(true);
    try {
      const res = await api.login({ email, password });
      api.setAuthToken(res.access_token);
      router.push(next);
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
        setPendingVerification(
          err.status === 403 &&
            (err.message.includes("未激活") || err.message.toLowerCase().includes("not verified"))
        );
      } else {
        setErrorMessage("登录失败");
      }
      setErrorDialogOpen(true);
    } finally {
      setLoading(false);
    }
  };

  const onResendVerification = async () => {
    if (!email || resendLoading) return;
    try {
      setResendLoading(true);
      setNotice("");
      await api.requestVerifyEmail(email);
      setNotice("激活邮件已发送，请前往邮箱完成激活后再登录");
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("发送激活邮件失败");
      }
      setErrorDialogOpen(true);
    } finally {
      setResendLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_10%_0%,#F9EEE8_0%,#F5F2EE_35%,#F3F1ED_100%)]">
      <div className="max-w-md mx-auto px-4 py-24">
        <div className="rounded-2xl border border-[#E5DED7] bg-white/90 backdrop-blur p-6 shadow-[0_18px_40px_rgba(31,27,24,0.08)]">
          <h1 className="text-2xl font-semibold text-[#1F1B18]">登录 AI 锦书</h1>
          <p className="mt-1 text-sm text-[#7E756D]">继续你的智能小说创作</p>
          <form className="mt-6 space-y-4" onSubmit={onSubmit}>
            <div>
              <label className="block text-sm text-[#5E5650] mb-1">邮箱</label>
              <input
                className="w-full h-10 rounded-lg border border-[#E5DED7] px-3 text-sm outline-none focus:border-[#C8211B]"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                type="email"
                required
              />
            </div>
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="block text-sm text-[#5E5650]">密码</label>
                <Link className="text-xs text-[#C8211B]" href="/auth/forgot-password">
                  忘记密码
                </Link>
              </div>
              <input
                className="w-full h-10 rounded-lg border border-[#E5DED7] px-3 text-sm outline-none focus:border-[#C8211B]"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                type="password"
                required
              />
            </div>
            <Button className="w-full" loading={loading} type="submit">
              登录
            </Button>
            {pendingVerification ? (
              <Button
                type="button"
                variant="secondary"
                className="w-full"
                loading={resendLoading}
                onClick={onResendVerification}
              >
                重新发送激活邮件
              </Button>
            ) : null}
            {notice ? <p className="text-xs text-[#18864B]">{notice}</p> : null}
          </form>
          <p className="mt-4 text-sm text-[#7E756D]">
            还没有账号？{" "}
            <Link className="text-[#C8211B]" href={`/auth/register${next ? `?next=${encodeURIComponent(next)}` : ""}`}>
              去注册
            </Link>
          </p>
        </div>
      </div>
      <ErrorDialog
        open={errorDialogOpen}
        onClose={() => setErrorDialogOpen(false)}
        title="登录失败"
        message={errorMessage || "请稍后重试"}
      />
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginPageContent />
    </Suspense>
  );
}
