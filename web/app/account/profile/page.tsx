"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, AuthUser, getErrorMessage } from "@/lib/api";
import { formatUserRole, formatUserStatus } from "@/lib/display";
import { Button } from "@/components/ui/Button";
import { ErrorDialog } from "@/components/ui/ErrorDialog";

export default function AccountProfilePage() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [notice, setNotice] = useState("");
  const [errorDialogOpen, setErrorDialogOpen] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const res = await api.me();
        if (mounted) setUser(res.user);
      } catch {
        if (mounted) setUser(null);
      } finally {
        if (mounted) setLoading(false);
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, []);

  const onChangePassword = async (e: FormEvent) => {
    e.preventDefault();
    setNotice("");
    setErrorMessage("");
    setErrorDialogOpen(false);
    if (!currentPassword || !newPassword || !confirmPassword) {
      setErrorMessage("请完整填写密码信息");
      setErrorDialogOpen(true);
      return;
    }
    if (newPassword !== confirmPassword) {
      setErrorMessage("两次输入的新密码不一致");
      setErrorDialogOpen(true);
      return;
    }
    try {
      setSaving(true);
      await api.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setNotice("密码修改成功");
    } catch (err) {
      setErrorMessage(getErrorMessage(err, "修改失败，请稍后重试"));
      setErrorDialogOpen(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <main className="min-h-screen bg-[#F7F5F2] px-6 py-8">
      <div className="mx-auto w-full max-w-[980px] space-y-6">
        <section className="rounded-2xl border border-[#E8E2DA] bg-white p-6">
          <h1 className="text-[26px] font-semibold text-[#1F1B18]">我的信息</h1>
          <p className="mt-1 text-sm text-[#8B8379]">用于查看账号状态、修改密码，后续可扩展更多个人设置</p>

          {loading ? (
            <p className="mt-4 text-sm text-[#8B8379]">加载中...</p>
          ) : (
            <div className="mt-5 grid gap-3 md:grid-cols-3">
              <div className="rounded-xl border border-[#E9E2D9] p-3">
                <p className="text-xs text-[#8B8379]">邮箱</p>
                <p className="mt-1 text-base font-medium text-[#1F1B18] break-all">{user?.email || "-"}</p>
              </div>
              <div className="rounded-xl border border-[#E9E2D9] p-3">
                <p className="text-xs text-[#8B8379]">角色</p>
                <p className="mt-1 text-base font-medium text-[#1F1B18]">{formatUserRole(user?.role)}</p>
              </div>
              <div className="rounded-xl border border-[#E9E2D9] p-3">
                <p className="text-xs text-[#8B8379]">状态</p>
                <p className="mt-1 text-base font-medium text-[#1F1B18]">{formatUserStatus(user?.status)}</p>
              </div>
            </div>
          )}
        </section>

        <section className="rounded-2xl border border-[#E8E2DA] bg-white p-6">
          <h2 className="text-xl font-semibold text-[#1F1B18]">修改密码</h2>
          <p className="mt-1 text-sm text-[#8B8379]">请输入当前密码并设置新密码</p>
          <form className="mt-5 grid gap-4 max-w-[520px]" onSubmit={onChangePassword}>
            <div>
              <label className="block text-sm text-[#5E5650] mb-1">当前密码</label>
              <input
                className="w-full h-10 rounded-lg border border-[#E5DED7] px-3 text-sm outline-none focus:border-[#C8211B]"
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="block text-sm text-[#5E5650] mb-1">新密码</label>
              <input
                className="w-full h-10 rounded-lg border border-[#E5DED7] px-3 text-sm outline-none focus:border-[#C8211B]"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
              />
            </div>
            <div>
              <label className="block text-sm text-[#5E5650] mb-1">确认新密码</label>
              <input
                className="w-full h-10 rounded-lg border border-[#E5DED7] px-3 text-sm outline-none focus:border-[#C8211B]"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
              />
            </div>

            {notice ? <p className="text-sm text-[#18864B]">{notice}</p> : null}

            <Button type="submit" className="w-fit" loading={saving}>
              保存新密码
            </Button>
          </form>
        </section>
      </div>

      <ErrorDialog
        open={errorDialogOpen}
        onClose={() => setErrorDialogOpen(false)}
        title="修改密码失败"
        message={errorMessage || "请稍后重试"}
      />
    </main>
  );
}
