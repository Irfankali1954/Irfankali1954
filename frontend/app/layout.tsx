import type { Metadata } from "next";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "EPC Master-Wrap Agent",
  description: "Cross-organizational intelligence layer for major EPCs",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: "system-ui, sans-serif" }}>
        <Nav />
        <main style={{ padding: 24 }}>{children}</main>
      </body>
    </html>
  );
}
