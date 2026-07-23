import type { ReactNode } from "react";
import {
  Activity,
  Bot,
  Database,
  Gauge,
  Grid2X2,
  Map,
  Settings,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

import "../styles/design.css";
import {
  controlCenterNavGroups,
  controlCenterNavItems,
  getControlCenterNavItem,
  type ControlCenterRouteId,
} from "./navigation";
import {
  BrandLockup,
  CompactKMark,
  HeaderControls,
  ShellStatusStrip,
  SystemStatusCard,
} from "../components/brand";

export type AppShellProps = {
  activeRoute: ControlCenterRouteId;
  children: ReactNode;
  title?: string;
  eyebrow?: string;
  subtitle?: string;
  actions?: ReactNode;
  statusStrip?: ReactNode;
  inspector?: ReactNode;
  utilityRail?: ReactNode;
  onNavigate?: (route: ControlCenterRouteId, href: string) => void;
};

const navIcons: Record<string, LucideIcon> = {
  activity: Activity,
  database: Database,
  gauge: Gauge,
  gear: Settings,
  grid: Grid2X2,
  map: Map,
  signal: Bot,
  spark: Sparkles,
};

export function AppShell({
  activeRoute,
  children,
  title,
  eyebrow,
  subtitle,
  actions,
  statusStrip,
  inspector,
  utilityRail,
  onNavigate,
}: AppShellProps) {
  const activeItem = getControlCenterNavItem(activeRoute);
  const resolvedTitle = title ?? activeItem.label;
  const resolvedEyebrow = eyebrow ?? "Kalshi Bot Control Center";
  const resolvedSubtitle = subtitle ?? activeItem.description;

  return (
    <div className="kbcc-shell">
      <a className="kbcc-skip-link" href="#kbcc-main">
        Skip to content
      </a>

      <aside className="kbcc-sidebar" aria-label="Primary navigation">
        <div className="kbcc-sidebar-top" aria-label="Kalshi Bot Control Center">
          <BrandLockup />
          <CompactKMark />
        </div>

        <nav className="kbcc-nav">
          {controlCenterNavGroups.map((group) => (
            <section className="kbcc-nav-group" key={group}>
              <div className="kbcc-nav-heading">{group}</div>
              {controlCenterNavItems
                .filter((item) => item.group === group)
                .map((item) => {
                  const isActive = item.id === activeRoute;

                  return (
                    <a
                      aria-current={isActive ? "page" : undefined}
                      className="kbcc-nav-item"
                      data-active={isActive ? "true" : "false"}
                      data-status={item.status ?? "ready"}
                      href={item.href}
                      key={item.id}
                      onClick={(event) => {
                        if (!onNavigate) {
                          return;
                        }

                        event.preventDefault();
                        onNavigate(item.id, item.href);
                      }}
                    >
                      <NavIcon name={item.icon} />
                      <span className="kbcc-nav-label">{item.label}</span>
                      <span className="kbcc-nav-short">{item.shortLabel}</span>
                      {item.status === "deferred" ? (
                        <span className="kbcc-nav-pill">Soon</span>
                      ) : null}
                    </a>
                  );
                })}
            </section>
          ))}
        </nav>

        <footer className="kbcc-sidebar-footer">
          <SystemStatusCard />
          <div className="kbcc-operator-card">
            <span className="kbcc-operator-avatar">AK</span>
            <div>
              <strong>ops_admin</strong>
              <span>Administrator</span>
            </div>
          </div>
          <div className="kbcc-shell-version">v1.0.0</div>
        </footer>
      </aside>

      <div className="kbcc-workspace">
        <header className="kbcc-header">
          <div className="kbcc-header-copy">
            <span className="kbcc-eyebrow">{resolvedEyebrow}</span>
            <h1>{resolvedTitle}</h1>
            <p>{resolvedSubtitle}</p>
          </div>
          <div className="kbcc-header-right">
            <ShellStatusStrip>{statusStrip}</ShellStatusStrip>
            {actions ? <div className="kbcc-header-actions">{actions}</div> : null}
            <HeaderControls />
          </div>
        </header>

        <main className="kbcc-main" id="kbcc-main">
          <div className="kbcc-content">{children}</div>
          {inspector ? (
            <aside className="kbcc-inspector" aria-label="Context inspector">
              {inspector}
            </aside>
          ) : null}
          {utilityRail ? <aside className="kbcc-utility-rail">{utilityRail}</aside> : null}
        </main>
      </div>

      <nav className="kbcc-bottom-nav" aria-label="Primary navigation">
        {controlCenterNavItems.slice(0, 5).map((item) => (
          <a
            aria-current={item.id === activeRoute ? "page" : undefined}
            className="kbcc-bottom-nav-item"
            data-active={item.id === activeRoute ? "true" : "false"}
            href={item.href}
            key={item.id}
            onClick={(event) => {
              if (!onNavigate) {
                return;
              }

              event.preventDefault();
              onNavigate(item.id, item.href);
            }}
          >
            <NavIcon name={item.icon} />
            <span>{item.shortLabel}</span>
          </a>
        ))}
      </nav>
    </div>
  );
}

function NavIcon({ name }: { name: string }) {
  const Icon = navIcons[name] ?? Gauge;

  return (
    <span className="kbcc-nav-icon" aria-hidden="true">
      <Icon size={16} strokeWidth={2.1} />
    </span>
  );
}
