import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.THOTH_API_ORIGIN ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/rpc": backend,
      "/files": backend,
      "/operations": backend,
      "/healthz": backend,
    },
  },
});
