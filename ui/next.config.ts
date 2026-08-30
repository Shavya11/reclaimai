import path from "node:path";
import type { NextConfig } from "next";

// Static export. The FastAPI process serves `ui/out` directly, so the demo is
// one command and one port — no second server to start, no proxy to configure,
// and nothing that can be running the wrong version of the other half.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: false,

  // Turbopack infers the project root by walking up for a lockfile. Nothing
  // above this directory is a JS project today, so the inference is right by
  // accident; naming the root keeps it right if that ever stops being true.
  // (This was not what broke the Vercel builds — that was an unset root
  // directory on the Vercel project itself. See DEPLOY.md §2.)
  turbopack: { root: path.resolve(import.meta.dirname) },
};

export default nextConfig;
