import type { NextConfig } from "next";

// The browser only ever calls same-origin /api/*, which app/api/[...path]/route.ts forwards to the
// FastAPI backend at request time (decisions D-06, D-98). BACKEND_URL is server-side
// configuration, never a secret, and never needed by client code. On Vercel it comes from the
// backend service binding, which exists only at runtime, so nothing here depends on it.

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
