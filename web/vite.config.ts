import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.PARTYLINKS_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/events": api, "/images": api, "/categories": api } },
});
