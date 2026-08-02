"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useDevice } from "@/hooks/useDevice";
import type { ReactNode } from "react";

/* Lets the sidebar dismiss the drawer after a nav click without every layout
   threading a callback down through WorkspaceSidebar/UtilitySidebar. Null on
   desktop and anywhere outside AppShell, so `drawer?.close()` is a no-op there
   rather than a crash. */
const SidebarDrawerContext = createContext<{ close: () => void } | null>(null);

export function useSidebarDrawer() {
  return useContext(SidebarDrawerContext);
}

interface AppShellProps {
  /** The route group's sidebar (workspace or utility). */
  sidebar: ReactNode;
  children: ReactNode;
}

/**
 * The app frame, shared by the (workspace) and (utility) route groups.
 *
 * Two layouts, picked by width:
 *
 *   >= 1024px  sidebar and content are siblings in a flex row — unchanged from
 *              what this app has always rendered.
 *   <  1024px  the sidebar leaves the flow entirely and becomes an overlay
 *             drawer behind a scrim, with a compact top bar owning the toggle.
 *             This keeps both phones and portrait tablets from sacrificing the
 *             conversation column to a fixed 220px rail.
 *
 * The split is expressed in CSS (`max-lg:` / `lg:`), not in `useDevice()`, so
 * the very first server-rendered paint is already correct on a narrow screen.
 * JS owns stateful behaviour: whether the drawer is open and where keyboard
 * focus should live while it is open.
 */
export default function AppShell({ sidebar, children }: AppShellProps) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const { isCompact } = useDevice();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const drawerRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const wasDrawerOpenRef = useRef(false);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  const close = useCallback(() => setDrawerOpen(false), []);

  // Any route change hands the screen back to the content. Compared during
  // render rather than in an effect (same pattern as SessionViewerPanel's
  // session reset) so the drawer never paints open over the new route.
  const [trackedPathname, setTrackedPathname] = useState(pathname);
  if (trackedPathname !== pathname) {
    setTrackedPathname(pathname);
    setDrawerOpen(false);
  }

  useEffect(() => {
    if (!isCompact || !drawerOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
      if (event.key !== "Tab") return;

      const focusable = drawerRef.current
        ? Array.from(
            drawerRef.current.querySelectorAll<HTMLElement>(
              'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
            ),
          ).filter((element) => !element.hasAttribute("aria-hidden"))
        : [];
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [drawerOpen, isCompact]);

  useEffect(() => {
    if (!isCompact) {
      if (wasDrawerOpenRef.current) {
        wasDrawerOpenRef.current = false;
        const resetFrame = requestAnimationFrame(() => setDrawerOpen(false));
        return () => cancelAnimationFrame(resetFrame);
      }
      return;
    }

    if (drawerOpen && !wasDrawerOpenRef.current) {
      const active = document.activeElement;
      restoreFocusRef.current =
        active instanceof HTMLElement ? active : menuButtonRef.current;
      requestAnimationFrame(() => {
        const first = drawerRef.current?.querySelector<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        );
        first?.focus();
      });
    } else if (!drawerOpen && wasDrawerOpenRef.current) {
      requestAnimationFrame(() => {
        (restoreFocusRef.current ?? menuButtonRef.current)?.focus();
      });
      restoreFocusRef.current = null;
    }
    wasDrawerOpenRef.current = drawerOpen;
  }, [drawerOpen, isCompact]);

  return (
    <SidebarDrawerContext.Provider value={{ close }}>
      {/* dvh, not vh: iOS Safari's 100vh includes the retracted address bar, so
          a vh-sized shell pushes the composer under it. */}
      <div className="flex h-dvh overflow-hidden">
        {drawerOpen ? (
          <div
            onClick={close}
            aria-hidden
            className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          />
        ) : null}

        {/* `inert` (not just translate-x) while closed: a drawer parked
            off-screen still holds ~20 focusable nav items, and without this
            Tab walks the user into a sidebar they cannot see. This is the
            half `max-lg:` cannot express, hence useDevice(). */}
        <div
          ref={drawerRef}
          data-testid="responsive-sidebar-drawer"
          role={isCompact ? "dialog" : undefined}
          aria-modal={isCompact && drawerOpen ? true : undefined}
          aria-label={isCompact ? t("Open navigation") : undefined}
          inert={isCompact && !drawerOpen ? true : undefined}
          className={`max-lg:fixed max-lg:inset-y-0 max-lg:left-0 max-lg:z-50 max-lg:shadow-xl max-lg:transition-transform max-lg:duration-200 max-lg:ease-out ${
            drawerOpen ? "max-lg:translate-x-0" : "max-lg:-translate-x-full"
          }`}
        >
          {sidebar}
        </div>

        <main
          inert={isCompact && drawerOpen ? true : undefined}
          className="flex min-w-0 flex-1 flex-col overflow-hidden bg-[var(--background)]"
        >
          <div className="flex h-11 shrink-0 items-center gap-1 border-b border-[var(--border)] px-2 lg:hidden">
            <button
              ref={menuButtonRef}
              data-testid="mobile-nav-toggle"
              type="button"
              onClick={() => setDrawerOpen(true)}
              aria-label={t("Open navigation")}
              aria-expanded={drawerOpen}
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
            >
              <Menu size={18} strokeWidth={1.7} />
            </button>
            <Link href="/" className="flex items-center gap-1.5">
              <Image
                src="/logo.png"
                alt="DeepTutor"
                width={20}
                height={20}
                className="h-5 w-5"
              />
              <Image
                src="/banner.png"
                alt="DeepTutor"
                width={897}
                height={236}
                className="h-[18px] w-auto"
              />
            </Link>
          </div>

          <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
        </main>
      </div>
    </SidebarDrawerContext.Provider>
  );
}
