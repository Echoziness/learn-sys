"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/sessions/new", label: "新建会话" },
  { href: "/sessions", label: "历史会话" },
  { href: "/resources", label: "资源库" },
];

/** 全站顶栏：毛玻璃吸顶 + 激活态胶囊导航——设计系统唯一导航实例 */
export function SiteNav() {
  const pathname = usePathname();
  const isActive = (href: string) => {
    if (href === "/sessions/new") return pathname === "/sessions/new";
    if (href === "/sessions") return pathname.startsWith("/sessions") && pathname !== "/sessions/new";
    return pathname.startsWith(href);
  };

  return (
    <header className="sticky top-0 z-50 border-b border-border/60 bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-8 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
          <span className="size-2 rounded-full bg-foreground" aria-hidden />
          learn-sys
        </Link>
        <nav className="flex items-center gap-1">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              className={cn(
                "rounded-full px-3 py-1.5 text-sm transition-colors duration-150",
                isActive(l.href)
                  ? "bg-foreground font-medium text-background"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              )}
            >
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
