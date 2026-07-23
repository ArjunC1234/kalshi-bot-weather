import type { CSSProperties, ReactNode } from "react";
import {
  Activity,
  Bell,
  Bot,
  CandlestickChart,
  CheckCircle2,
  CloudRain,
  Cpu,
  Gauge,
  RadioTower,
  RefreshCw,
  ShieldCheck,
  TrendingUp,
  UserCircle2,
} from "lucide-react";

export type BrandMarkSize = "sm" | "md" | "lg";

export function BrandMark({ size = "md" }: { size?: BrandMarkSize }) {
  return (
    <span className="kbcc-brandmark" data-size={size} aria-hidden="true">
      <span className="kbcc-brandmark-ring" />
      <span className="kbcc-brandmark-axis kbcc-brandmark-axis-x" />
      <span className="kbcc-brandmark-axis kbcc-brandmark-axis-y" />
      <CloudRain className="kbcc-brandmark-weather" size={20} strokeWidth={2.1} />
      <TrendingUp className="kbcc-brandmark-trend" size={27} strokeWidth={2.4} />
      <CandlestickChart className="kbcc-brandmark-candles" size={18} strokeWidth={2.2} />
      <span className="kbcc-brandmark-node kbcc-brandmark-node-a" />
      <span className="kbcc-brandmark-node kbcc-brandmark-node-b" />
      <span className="kbcc-brandmark-node kbcc-brandmark-node-c" />
      <span className="kbcc-brandmark-node kbcc-brandmark-node-d" />
    </span>
  );
}

export type BrandLockupProps = {
  compact?: boolean;
  subtitle?: string;
  title?: string;
};

export function BrandLockup({
  compact = false,
  subtitle = "Bot Control Center",
  title = "Kalshi",
}: BrandLockupProps) {
  return (
    <div className="kbcc-brand-lockup" data-compact={compact ? "true" : "false"}>
      <BrandMark size={compact ? "sm" : "md"} />
      <div className="kbcc-brand-lockup-copy">
        <strong>{title}</strong>
        <span>{subtitle}</span>
      </div>
    </div>
  );
}

export function CompactKMark() {
  return (
    <span className="kbcc-kmark" aria-hidden="true">
      K
    </span>
  );
}

export type StatusTone = "healthy" | "warning" | "danger" | "info" | "muted";

export function StatusDot({ tone = "healthy" }: { tone?: StatusTone }) {
  return <span className="kbcc-status-dot" data-tone={tone} aria-hidden="true" />;
}

export type SystemStatusCardProps = {
  label?: string;
  status?: string;
  tone?: StatusTone;
};

export function SystemStatusCard({
  label = "System Ready",
  status = "All systems operational",
  tone = "healthy",
}: SystemStatusCardProps) {
  return (
    <div className="kbcc-system-card" data-tone={tone}>
      <ShieldCheck size={22} strokeWidth={2.1} aria-hidden="true" />
      <div>
        <strong>{label}</strong>
        <span>{status}</span>
      </div>
    </div>
  );
}

export type ShellStatusItemProps = {
  icon?: "api" | "compute" | "storage" | "queue";
  label: string;
  value: string;
  tone?: StatusTone;
  meter?: number;
};

const statusIcons = {
  api: RadioTower,
  compute: Cpu,
  queue: Activity,
  storage: Gauge,
};

export function ShellStatusItem({
  icon = "api",
  label,
  value,
  tone = "healthy",
  meter,
}: ShellStatusItemProps) {
  const Icon = statusIcons[icon];

  return (
    <div className="kbcc-shell-status-item" data-tone={tone}>
      <Icon size={16} strokeWidth={2.1} aria-hidden="true" />
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        {typeof meter === "number" ? (
          <i
            className="kbcc-shell-status-meter"
            style={{ "--kbcc-meter-value": `${Math.max(0, Math.min(100, meter))}%` } as CSSProperties}
            aria-hidden="true"
          />
        ) : null}
      </div>
    </div>
  );
}

export function ShellStatusStrip({ children }: { children?: ReactNode }) {
  return (
    <div className="kbcc-shell-status-strip">
      {children ?? (
        <>
          <ShellStatusItem icon="compute" label="Compute" value="Healthy" meter={78} />
          <ShellStatusItem icon="storage" label="Storage" value="Ready" meter={64} />
          <ShellStatusItem icon="api" label="API" value="Online" />
        </>
      )}
    </div>
  );
}

export function HeaderControls() {
  return (
    <div className="kbcc-header-controls" aria-label="Control Center status controls">
      <button className="kbcc-icon-button" type="button" aria-label="Refresh view">
        <RefreshCw size={16} strokeWidth={2.2} aria-hidden="true" />
      </button>
      <button className="kbcc-icon-button" type="button" aria-label="Notifications">
        <Bell size={16} strokeWidth={2.2} aria-hidden="true" />
        <span className="kbcc-notification-badge">3</span>
      </button>
      <button className="kbcc-user-chip" type="button" aria-label="Current user menu">
        <UserCircle2 size={20} strokeWidth={2.1} aria-hidden="true" />
        <span>AK</span>
      </button>
    </div>
  );
}

export function BotAvatar() {
  return (
    <span className="kbcc-bot-avatar" aria-hidden="true">
      <Bot size={18} strokeWidth={2.2} />
    </span>
  );
}
