"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { ErrorDialog } from "@/components/ui/ErrorDialog";

function RegisterPageContent() {
  const router = useRouter();
  const search = useSearchParams();
  const next = search.get("next") || "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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
      const res = await api.register({ email, password });
      if (res.access_token) {
        api.setAuthToken(res.access_token);
        router.push(next);
      } else {
        setNotice(res.message || "注册成功，请查收激活邮件");
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("注册失败");
      }
      setErrorDialogOpen(true);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_0%_0%,#FBEDEA_0%,#F5F1ED_35%,#F2F0EC_100%)]">
      <div className="max-w-md mx-auto px-4 py-24">
        <div className="rounded-2xl border border-[#E5DED7] bg-white/90 p-6 shadow-[0_18px_40px_rgba(31,27,24,0.08)]">
          <h1 className="text-2xl font-semibold text-[#1F1B18]">注册 AI 锦书</h1>
          <p className="mt-1 text-sm text-[#7E756D]">创建账号开始自动写小说</p>
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
              <label className="block text-sm text-[#5E5650] mb-1">密码</label>
              <input
                className="w-full h-10 rounded-lg border border-[#E5DED7] px-3 text-sm outline-none focus:border-[#C8211B]"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                type="password"
                required
              />
              <p className="mt-1 text-xs text-[#8E8379]">至少10位，包含大小写字母、数字和特殊字符</p>
            </div>
            {notice ? <p className="text-xs text-[#18864B]">{notice}</p> : null}
            <Button className="w-full" loading={loading} type="submit">
              注册
            </Button>
          </form>
          <p className="mt-4 text-sm text-[#7E756D]">
            已有账号？{" "}
            <Link className="text-[#C8211B]" href={`/auth/login${next ? `?next=${encodeURIComponent(next)}` : ""}`}>
              去登录
            </Link>
          </p>
        </div>
      </div>
      <ErrorDialog
        open={errorDialogOpen}
        onClose={() => setErrorDialogOpen(false)}
        title="注册失败"
        message={errorMessage || "请稍后重试"}
      />
    </main>
  );
}

export default function RegisterPage() {
  return (
    <Suspense fallback={null}>
      <RegisterPageContent />
    </Suspense>
  );
}
