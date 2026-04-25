import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* Docker: emit a self-contained `node server.js` runtime */
  output: "standalone",
};

export default nextConfig;
