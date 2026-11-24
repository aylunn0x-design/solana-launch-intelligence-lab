import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // Emits .next/standalone with a self-contained server.js, which the
  // Dockerfile's runtime stage copies. Without this the image has no server.js.
  output: "standalone",
  // The API base URL is read at runtime from NEXT_PUBLIC_API_BASE_URL so the
  // same build can point at a local backend or a deployed one.
  env: {
    NEXT_PUBLIC_API_BASE_URL:
      process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
  },
};

export default config;
