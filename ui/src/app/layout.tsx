import type { Metadata, Viewport } from "next";
import { JetBrains_Mono, Plus_Jakarta_Sans } from "next/font/google";
import "./globals.css";

// Self-hosted at build time, so the static export carries its own fonts and
// the demo does not depend on a font CDN being reachable in the room.
const display = Plus_Jakarta_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--font-display",
  display: "swap",
});

const numeric = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-numeric",
  display: "swap",
});

export const metadata: Metadata = {
  title: "ReclaimAI — revenue recovery agent",
  description:
    "Detects revenue at risk, diagnoses why it failed, decides a bounded intervention, and refuses to act when it should not.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

// Runs before paint so a reload never flashes the wrong theme. Wrapped because
// storage throws outright in some embedded contexts.
const THEME_BOOT = `try{var t=localStorage.getItem("reclaim-theme");if(t==="dark"||t==="light")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body
        className={`${display.variable} ${numeric.variable} min-h-screen antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
