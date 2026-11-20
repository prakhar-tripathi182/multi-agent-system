import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server runs on 5173, which the backend's CORS setting allows.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
