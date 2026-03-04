"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { BookCopy, ChevronDown, LogOut, Plus, Star, Type, UserCircle2 } from "lucide-react";
import { api, AuthUser, HeaderStats } from "@/lib/api";
import { formatUserRole, formatUserStatus } from "@/lib/display";
import { Button } from "@/components/ui/Button";

export function AppHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const [stats, setStats] = useState<HeaderStats>({
    works: 0,
    week_chapters: 0,
    quality_score: 0,
    total_words: 0,
  });
  const [user, setUser] = useState<AuthUser | null>(null);
  const [logoutLoading, setLogoutLoading] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let disposed = false;
    const loadStats = async () => {
      if (pathname.startsWith("/auth") || !api.getAuthToken()) {
        if (!disposed) {
          setStats({
            works: 0,
            week_chapters: 0,
            quality_score: 0,
            total_words: 0,
          });
        }
        return;
      }
      try {
        const data = await api.getHeaderStats();
        if (!disposed) setStats(data);
      } catch {
        // keep stale data to avoid layout jump
      }
    };
    void loadStats();
    const timer = setInterval(loadStats, 15000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [pathname]);

  useEffect(() => {
    let disposed = false;
    const loadUser = async () => {
      if (!api.getAuthToken()) {
        if (!disposed) setUser(null);
        return;
      }
      try {
        const res = await api.me();
        if (!disposed) setUser(res.user);
      } catch {
        if (!disposed) setUser(null);
      }
    };
    if (!pathname.startsWith("/auth")) loadUser();
    return () => {
      disposed = true;
    };
  }, [pathname]);

  useEffect(() => {
    const onDocClick = (event: MouseEvent) => {
      if (!menuRef.current) return;
      if (!menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  return (
    <header className="sticky top-0 z-50 border-b border-[#DDD8D3] bg-[#F8F6F3]/95 backdrop-blur-xl">
      <div className="max-w-[1500px] mx-auto px-3 h-14 flex items-center gap-3">
        <div className="flex items-center gap-4 min-w-0">
          <Link href="/" className="inline-flex items-center gap-2 shrink-0">
            <div className="w-7 h-7 rounded-[8px] bg-[#C8211B] text-white flex items-center justify-center font-semibold text-sm">锦</div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-[#1F1B18] text-[18px] leading-none">锦书</span>
              <span className="hidden lg:inline-flex h-5 items-center rounded-full border border-[#E6DED6] bg-[#F9F6F2] px-2 text-[11px] leading-none text-[#7A7068]">
                AI 智能写小说平台
              </span>
            </div>
          </Link>
          <nav className="hidden md:flex items-center gap-6 text-[15px]">
            <NavItem href="/" active={pathname === "/"}>工作台</NavItem>
            <NavItem href="/novels" active={pathname.startsWith("/novels")}>我的作品</NavItem>
            <NavItem href="/storyboards" active={pathname.startsWith("/storyboards")}>导演分镜</NavItem>
            {user?.role === "admin" ? (
              <>
                <NavItem href="/admin/users" active={pathname.startsWith("/admin/users")}>用户管理</NavItem>
                <NavItem href="/admin/settings" active={pathname.startsWith("/admin/settings")}>系统设置</NavItem>
              </>
            ) : null}
          </nav>
        </div>

        <div className="ml-auto flex items-center gap-5 shrink-0">
          <div className="hidden xl:flex items-center gap-3 text-[13px] text-[#3E3833] whitespace-nowrap">
            <StatItem icon={<BookCopy className="w-3.5 h-3.5" />} text={`${stats.works} 作品`} />
            <StatItem icon={<BookCopy className="w-3.5 h-3.5 rotate-45" />} text={`${stats.week_chapters} 本周章节`} />
            <StatItem icon={<Star className="w-3.5 h-3.5" />} text={`${stats.quality_score.toFixed(1)} 质量分`} />
            <StatItem icon={<Type className="w-3.5 h-3.5" />} text={`${formatWordCount(stats.total_words)} 总字数`} />
          </div>
          <Link href="/create">
            <Button size="sm" className="h-9 px-4 bg-[#C8211B] hover:bg-[#AD1B16] shadow-none text-sm rounded-[10px]">
              <Plus className="w-4 h-4 mr-1" />
              新建
            </Button>
          </Link>
          {!pathname.startsWith("/auth") ? (
            <div className="relative" ref={menuRef}>
              <button
                type="button"
                className="h-9 px-3 text-sm border border-[#DDD8D3] hover:bg-[#F2EEEA] rounded-[10px] inline-flex items-center gap-2 text-[#3E3833]"
                onClick={() => setMenuOpen((v) => !v)}
              >
                <UserCircle2 className="w-4 h-4" />
                <span>个人中心</span>
                <ChevronDown className={`w-4 h-4 transition-transform ${menuOpen ? "rotate-180" : ""}`} />
              </button>

              {menuOpen ? (
                <div className="absolute right-0 top-11 w-72 rounded-xl border border-[#E6DED6] bg-white shadow-[0_16px_48px_rgba(31,27,24,0.16)] p-2">
                  <div className="px-3 py-2 border-b border-[#F0EAE2]">
                    <p className="text-xs text-[#8B8379]">当前账号</p>
                    <p className="text-sm text-[#1F1B18] font-medium truncate">{user?.email || "未登录"}</p>
                    <p className="text-xs text-[#8B8379] mt-0.5">
                      角色：{formatUserRole(user?.role)} · 状态：{formatUserStatus(user?.status)}
                    </p>
                  </div>

                  <div className="py-1">
                    <Link
                      href="/account/profile"
                      className="h-9 px-3 rounded-lg text-sm text-[#2E2823] hover:bg-[#F6F3EF] flex items-center"
                      onClick={() => setMenuOpen(false)}
                    >
                      我的信息
                    </Link>
                    <Link
                      href="/account"
                      className="h-9 px-3 rounded-lg text-sm text-[#2E2823] hover:bg-[#F6F3EF] flex items-center"
                      onClick={() => setMenuOpen(false)}
                    >
                      账户与账单
                    </Link>
                  </div>

                  <button
                    type="button"
                    className="w-full h-9 px-3 rounded-lg text-sm text-[#B0372F] hover:bg-[#FFF1F0] flex items-center"
                    onClick={async () => {
                      try {
                        setLogoutLoading(true);
                        await api.logout();
                      } finally {
                        api.setAuthToken(null);
                        setLogoutLoading(false);
                        setMenuOpen(false);
                        router.push("/auth/login");
                      }
                    }}
                    disabled={logoutLoading}
                  >
                    <LogOut className="w-4 h-4 mr-2" />
                    {logoutLoading ? "退出中..." : "退出登录"}
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}

function NavItem({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={`relative inline-flex items-center h-8 px-0.5 font-medium transition-colors ${
        active ? "text-[#C8211B]" : "text-[#6B635D] hover:text-[#1F1B18]"
      }`}
    >
      {children}
      <span
        className={`absolute left-0 right-0 -bottom-0.5 h-[2px] rounded-full transition-opacity ${
          active ? "bg-[#C8211B] opacity-100" : "opacity-0"
        }`}
      />
    </Link>
  );
}

function StatItem({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[#403A34]">
      {icon}
      {text}
    </span>
  );
}

function formatWordCount(words: number) {
  if (!words) return "0";
  if (words >= 10000) return `${(words / 10000).toFixed(1)}万`;
  return `${Math.round(words).toLocaleString()}`;
}
