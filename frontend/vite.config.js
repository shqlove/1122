import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
export default defineConfig({
  plugins: [react()],
  base: "./",
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: "../dialogue_tool/ui/app",
    emptyOutDir: true,
    assetsInlineLimit: 100000,
    rollupOptions: {
      output: {
        entryFileNames: "workbench.js",
        assetFileNames: "workbench.[ext]",
      },
    },
  },
});
