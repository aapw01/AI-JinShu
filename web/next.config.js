/** @type {import('next').NextConfig} */
const apiTarget = (process.env.API_TARGET_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiTarget}/api/:path*`,
      },
      {
        source: "/health",
        destination: `${apiTarget}/health`,
      },
    ];
  },
};

module.exports = nextConfig;
