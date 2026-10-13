import type { NextConfig } from "next";

// The browser only ever calls same-origin /api/*; Next.js proxies it to the FastAPI
// backend (decision D-06). BACKEND_URL is server-side configuration, never a secret,
// and never needed by client code.
const backendUrl = (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");

// On Vercel the backend cannot be on localhost: fail the build with a clear message instead of
// shipping a site whose /api calls all fail. (The rewrite is fixed at build time.)
if (process.env.VERCEL && !/^https:\/\//.test(process.env.BACKEND_URL ?? "")) {
  throw new Error(
    "BACKEND_URL must be set to the public https URL of the MarketLens backend (Vercel project > Settings > Environment Variables). See docs/DEPLOY_VERCEL.md.",
  );
}

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self)" },
        ],
      },
    ];
  },
};

export default nextConfig;
