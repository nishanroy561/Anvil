/** @type {import('next').NextConfig} */
const API_BASE = process.env.WEB_API_BASE_URL ?? "http://localhost:8000";

const nextConfig = {
  output: "standalone",
  // The browser talks to the API through this same-origin proxy so the session
  // cookie is first-party and CORS never enters the picture. WEB_API_BASE_URL
  // points at the API from the *web server's* network (compose service name in
  // Docker, localhost in dev).
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_BASE}/:path*` }];
  },
};

export default nextConfig;
