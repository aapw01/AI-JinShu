"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/Button";

function VerifyPageContent() {
  const search = useSearchParams();
  const token = search.get("token") || "";
  const [state, setState] = useState<"loading" | "success" | "failed">("loading");
  const [message, setMessage] = useState("正在验证邮箱...");

  useEffect(() => {
    let mounted = true;
    const run = async () => {
      if (!token) {
        setState("failed");
        setMessage("缺少验证令牌");
        return;
      }
      try {
        await api.confirmVerifyEmail(token);
        if (!mounted) return;
        setState("success");
        setMessage("邮箱已验证，可以登录。");
      } catch (err) {
        if (!mounted) return;
        setState("failed");
        if (err instanceof ApiError) setMessage(err.message);
        else setMessage("验证失败");
      }
    };
    run();
    return () => {
      mounted = false;
    };
  }, [token]);

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_0%_0%,#FBEDEA_0%,#F5F1ED_35%,#F2F0EC_100%)]">
      <div className="max-w-md mx-auto px-4 py-24">
        <div className="rounded-2xl border border-[#E5DED7] bg-white/90 p-6 shadow-[0_18px_40px_rgba(31,27,24,0.08)]">
          <h1 className="text-2xl font-semibold text-[#1F1B18]">邮箱验证</h1>
          <p className={`mt-4 text-sm ${state === "failed" ? "text-[#C4372D]" : "text-[#5E5650]"}`}>{message}</p>
          <div className="mt-6">
            <Link href="/auth/login">
              <Button className="w-full">{state === "success" ? "去登录" : "返回登录"}</Button>
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}

export default function VerifyPage() {
  return (
    <Suspense fallback={null}>
      <VerifyPageContent />
    </Suspense>
  );
}
