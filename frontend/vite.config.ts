import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), ["VITE_", "API_PROXY_"]);
  const apiProxyTarget = env.API_PROXY_TARGET || "http://localhost:8000";

  return {
    plugins: [tailwindcss(), react()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    server: {
      host: true,
      port: 3000,
      proxy: {
        "/api": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
        // The external tier sits on the backend at the bare root, apart from
        // /api: the MCP endpoint (/mcp) and the Public API (/public/v1/...).
        // Proxying both makes origin-based addresses ({origin}/mcp) actually
        // resolve in dev, matching prod where nginx fronts them on one origin.
        "/mcp": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
        "/public": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
