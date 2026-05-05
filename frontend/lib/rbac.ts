import type { Role } from "./api";

/** UI-side awareness of the federated RBAC model.
 *
 * Authoritative checks happen on the server. These constants only drive
 * navigation — the API still strips fields the user is not allowed to see.
 */
export const NAV_ITEMS: { href: string; label: string; roles: Role[] }[] = [
  { href: "/cfo",            label: "CFO Command Center", roles: ["cfo", "project_director"] },
  { href: "/cfo/visibility", label: "Visibility Policy",  roles: ["cfo"] },
  { href: "/cfo/alerts",     label: "Senior Alert List",  roles: ["cfo"] },
  { href: "/cfo/exposure",   label: "Net Exposure",       roles: ["cfo", "project_director"] },
  { href: "/notifications",  label: "Alerts Feed",        roles: ["admin", "cfo", "project_director", "epc_manager"] },
  { href: "/erp",            label: "ERP / Gantt Bridge", roles: ["admin", "cfo", "project_director", "epc_manager"] },
  { href: "/schedule",       label: "Schedule",           roles: ["admin", "project_director", "epc_manager", "site_manager", "civil_engineer", "subcontractor", "supplier"] },
  { href: "/risk",           label: "Wrap Risk",          roles: ["admin", "cfo", "project_director", "epc_manager"] },
  { href: "/claims",         label: "Delay Claims",       roles: ["cfo", "project_director", "epc_manager"] },
  { href: "/change-orders",  label: "Change Orders",      roles: ["cfo", "project_director", "epc_manager", "subcontractor"] },
  { href: "/messages",       label: "Messages",           roles: ["admin", "cfo", "project_director", "epc_manager", "site_manager", "civil_engineer", "subcontractor", "supplier"] },
  { href: "/admin",          label: "Admin",              roles: ["admin"] },
];

export function visibleNavFor(role: Role | null) {
  if (!role) return [];
  return NAV_ITEMS.filter((i) => i.roles.includes(role));
}
