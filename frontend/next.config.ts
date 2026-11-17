import type { NextConfig } from "next";

// Next.js dev server blocks cross-origin requests (including the HMR
// websocket) from any host not in this list, to guard against DNS
// rebinding. Set NEXT_ALLOWED_DEV_ORIGINS (comma-separated hostnames, no
// protocol/port) to access the dev server from a LAN IP or custom hostname.
const allowedDevOrigins = process.env.NEXT_ALLOWED_DEV_ORIGINS
  ? process.env.NEXT_ALLOWED_DEV_ORIGINS.split(",").map((s) => s.trim()).filter(Boolean)
  : undefined;

function loopbackHttpTarget(raw: string): boolean {
  try {
    const url = new URL(raw);
    return url.protocol === 'http:' &&
      (url.hostname === '127.0.0.1' || url.hostname === '[::1]') &&
      !url.username && !url.password && url.pathname === '/' && !url.search && !url.hash;
  } catch {
    return false;
  }
}

const nextConfig: NextConfig = {
  // Enable standalone output for optimized Docker deployment
  output: "standalone",

  ...(allowedDevOrigins && allowedDevOrigins.length > 0 ? { allowedDevOrigins } : {}),

  // Experimental features
  // Type assertion needed: proxyClientMaxBodySize is valid in Next.js 15 but types lag behind
  experimental: {
    // Increase proxy body size limit for file uploads (default is 10MB)
    // This allows larger files to be uploaded through the /api/* rewrite proxy to FastAPI
    proxyClientMaxBodySize: '100mb',
  } as NextConfig['experimental'],

  // API Rewrites: Proxy /api/* requests to FastAPI backend
  // This simplifies reverse proxy configuration - users only need to proxy to port 8502
  // Next.js handles internal routing to the API backend on port 5055
  async rewrites() {
    // INTERNAL_API_URL: Where Next.js server-side should proxy API requests
    // Default: http://localhost:5055 (single-container deployment)
    // Override for multi-container: INTERNAL_API_URL=http://api-service:5055
    const internalApiUrl = (process.env.INTERNAL_API_URL ||
      (process.env.OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE === '1'
        ? 'http://127.0.0.1:5055' : 'http://localhost:5055')).replace(/\/$/, '')
    const tagentInternalUrl = (process.env.TAGENT_INTERNAL_URL || 'http://127.0.0.1:5001').replace(/\/$/, '')
    if (process.env.OPEN_NOTEBOOK_TAGENT_ACCOUNT_MODE === '1' &&
        (!loopbackHttpTarget(internalApiUrl) || !loopbackHttpTarget(tagentInternalUrl))) {
      throw new Error('Account mode requires loopback HTTP API and identity targets')
    }

    console.log(`[Next.js Rewrites] Proxying /api/* to ${internalApiUrl}/api/*`)

    return [
      {
        source: '/agent-api/auth/:path*',
        destination: `${tagentInternalUrl}/auth/:path*`,
      },
      {
        source: '/api/:path*',
        destination: `${internalApiUrl}/api/:path*`,
      },
    ]
  },

  // Redirect the legacy credentials route to the canonical "Models" route so
  // existing bookmarks and links keep working.
  async redirects() {
    return [
      {
        source: '/settings/api-keys',
        destination: '/settings/models',
        permanent: true,
      },
    ]
  },
};

export default nextConfig;
