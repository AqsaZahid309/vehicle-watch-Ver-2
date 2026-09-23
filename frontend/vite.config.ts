import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the SPA runs on :5173 and proxies API calls to FastAPI on :8000.
// `npm run build` writes into app/static/app, which FastAPI serves in production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/health": "http://localhost:8000",
    },
  },
  build: {
    outDir: "../app/static/app",
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
});
