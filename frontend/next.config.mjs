/**
 * The browser talks to one address: in the demonstration, `/api/assistant` (the module) and
 * `/api/demo` (the demonstration bar's stand-ins) are passed on to the back end. Nothing is
 * loaded from another site.
 */
const backend = process.env.EA_BACKEND_URL ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  images: { unoptimized: true },
  async rewrites() {
    return [
      { source: "/api/assistant/:path*", destination: `${backend}/api/assistant/:path*` },
      { source: "/api/demo", destination: `${backend}/api/demo` },
      { source: "/api/demo/:path*", destination: `${backend}/api/demo/:path*` },
    ];
  },
};

export default nextConfig;
