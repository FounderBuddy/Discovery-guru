import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the api-gateway runs on :8000; the dev server proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000" } },
});
