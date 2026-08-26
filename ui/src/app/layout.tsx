import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ReclaimAI — revenue recovery agent",
  description:
    "Detects revenue at risk, diagnoses why it failed, decides a bounded intervention, and refuses to act when it should not.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
