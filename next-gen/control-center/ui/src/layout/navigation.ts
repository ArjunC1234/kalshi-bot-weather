export type ControlCenterRouteId =
  | "dashboard"
  | "exports"
  | "models"
  | "data"
  | "jobs"
  | "bot"
  | "settings";

export type ControlCenterNavStatus = "ready" | "deferred";

export type ControlCenterNavItem = {
  id: ControlCenterRouteId;
  label: string;
  shortLabel: string;
  description: string;
  href: string;
  group: "Operate" | "Analyze" | "System";
  icon: string;
  status?: ControlCenterNavStatus;
};

export const controlCenterNavItems: ControlCenterNavItem[] = [
  {
    id: "dashboard",
    label: "Dashboard",
    shortLabel: "Home",
    description: "Current exports, registry coverage, report freshness, and job state.",
    href: "/",
    group: "Operate",
    icon: "grid",
  },
  {
    id: "exports",
    label: "Export Dataset",
    shortLabel: "Export",
    description: "Create, validate, reduce, extend, and archive local research exports.",
    href: "/exports",
    group: "Operate",
    icon: "database",
  },
  {
    id: "models",
    label: "Model Lab",
    shortLabel: "Models",
    description: "Select registered models, run jobs, and inspect model reports.",
    href: "/models",
    group: "Analyze",
    icon: "spark",
  },
  {
    id: "data",
    label: "Data Explorer",
    shortLabel: "Data",
    description: "Inspect exported tables, cities, coverage, schemas, and trends.",
    href: "/data",
    group: "Analyze",
    icon: "map",
  },
  {
    id: "jobs",
    label: "Jobs",
    shortLabel: "Jobs",
    description: "Monitor running work, logs, cancellations, retries, and artifacts.",
    href: "/jobs",
    group: "System",
    icon: "activity",
  },
  {
    id: "bot",
    label: "Bot Monitor",
    shortLabel: "Bot",
    description: "Deployed bot telemetry, positions, decisions, and runtime health.",
    href: "/bot",
    group: "System",
    icon: "signal",
    status: "deferred",
  },
  {
    id: "settings",
    label: "Settings",
    shortLabel: "Config",
    description: "Registry locations, API base URL, display density, and theme behavior.",
    href: "/settings",
    group: "System",
    icon: "gear",
  },
];

export const controlCenterNavGroups = ["Operate", "Analyze", "System"] as const;

export function getControlCenterNavItem(
  id: ControlCenterRouteId,
): ControlCenterNavItem {
  return controlCenterNavItems.find((item) => item.id === id) ?? controlCenterNavItems[0];
}
