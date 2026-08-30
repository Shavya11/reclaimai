import path from "node:path";
import type { NextConfig } from "next";

// Static export. The FastAPI process serves `ui/out` directly, so the demo is
// one command and one port — no second server to start, no proxy to configure,
// and nothing that can be running the wrong version of the other half.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: false,

  // Turbopack infers the project root by walking up for a lockfile. On Vercel
  // the repo root sits one level above this directory and Vercel rewrites the
  // config before the build, and the inference landed there instead of here —
  // "Couldn't find any `pages` or `app` directory", on a tree that has one.
  // Locally the walk-up finds nothing and the same build succeeds, which is why
  // this only ever failed on the Git deployments. Say the root outright.
  turbopack: { root: path.resolve(import.meta.dirname) },
};

export default nextConfig;
