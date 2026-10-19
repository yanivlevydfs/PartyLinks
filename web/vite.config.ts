import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.PARTYLINKS_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(
      ["/events", "/images", "/categories", "/sources", "/runs", "/maintenance", "/admin/api"].map((p) => [p, api]),
    ),
  },
});
