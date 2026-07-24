import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server (npm run dev on :5173) proxies the API calls to the FastAPI
// backend on :8000, so cookies stay same-origin from the browser's view.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "^/(problem|new-problem|chat|history|progress)": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
