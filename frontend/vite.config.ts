import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // ws: true also proxies the live workflow WebSocket (/api/v1/ws/...). API_TARGET is used by the E2E suite.
    proxy: { "/api": { target: process.env.API_TARGET ?? "http://localhost:8000", ws: true } },
  },
});
