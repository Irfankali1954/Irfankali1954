"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { Role } from "@/lib/api";
import { visibleNavFor } from "@/lib/rbac";

export function Nav() {
  const [role, setRole] = useState<Role | null>(null);
  useEffect(() => {
    const r = window.localStorage.getItem("epc_role") as Role | null;
    setRole(r);
  }, []);
  const items = visibleNavFor(role);
  return (
    <nav style={{ display: "flex", gap: 16, padding: "12px 24px", borderBottom: "1px solid #ddd" }}>
      <strong style={{ marginRight: "auto" }}>EPC Master-Wrap</strong>
      {items.map((i) => (
        <Link key={i.href} href={i.href}>{i.label}</Link>
      ))}
      <span style={{ color: "#666" }}>{role ?? "anon"}</span>
    </nav>
  );
}
