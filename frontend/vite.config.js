import { defineConfig } from "vite";

/** Dev server proxies /api → FastAPI on port 8000 (run `python -m backend` from repo root). */
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
