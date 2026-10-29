import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { execSync } from "node:child_process";

// Footer version: the pushed commit (Railway passes its SHA to the Docker build; locally, git).
const sha = (process.env.RAILWAY_GIT_COMMIT_SHA || (() => {
  try { return execSync("git rev-parse HEAD").toString().trim(); } catch { return "dev"; }
})()).slice(0, 7);

const api = process.env.PARTYLINKS_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  define: { __VERSION__: JSON.stringify(`${sha} · ${new Date().toISOString().slice(0, 10)}`) },
  server: {
    proxy: Object.fromEntries(
      [
        "/events", "/images", "/categories", "/sources", "/runs", "/maintenance", "/admin/api", "/site",
        "/weather", "/locate", "/posts", "/ticket-sites", "/landing-info", "/go", "/health",
        "/plans", "/api", "/auth", "/artwork",
      ].map((p) => [p, api]),
    ),
  },
});
