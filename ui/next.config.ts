import type { NextConfig } from "next";

// Static export. The FastAPI process serves `ui/out` directly, so the demo is
// one command and one port — no second server to start, no proxy to configure,
// and nothing that can be running the wrong version of the other half.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: false,
};

export default nextConfig;
