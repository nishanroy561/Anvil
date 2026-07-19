/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  env: {
    WEB_API_BASE_URL: process.env.WEB_API_BASE_URL ?? "http://localhost:8000",
  },
};

export default nextConfig;
