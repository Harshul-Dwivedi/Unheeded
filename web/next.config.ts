import path from "node:path";
import type { NextConfig } from "next";

const API = process.env.API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  turbopack: { root: path.join(__dirname) },  // a stray package-lock.json in $HOME would otherwise become the root
  devIndicators: false,  // no dev badge in screenshots and the demo recording
  // the browser only ever talks to this app; /api is proxied to FastAPI, so no CORS is needed
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};

export default config;
