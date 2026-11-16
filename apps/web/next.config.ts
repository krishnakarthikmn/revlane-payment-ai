import type { NextConfig } from "next";

// The console is client-rendered from in-memory state, so the generated
// `cacheComponents` and `partialPrefetching` options are left off: their
// instant-navigation check rejects a layout that waits for the fixtures.
const nextConfig: NextConfig = {
  // The browser tests run their own dev server beside `make dev`, in their own folder.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
