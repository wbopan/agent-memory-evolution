import type { Config } from "tailwindcss";
import { heroui } from "@heroui/react";

// HeroUI ships its own bundled copy of tailwindcss types, which differ from the
// project's Tailwind v3 PluginAPI. The plugin is runtime-compatible; cast it.
type TWPlugin = NonNullable<Config["plugins"]>[number];

const config: Config = {
  content: [
    "./src/**/*.{js,ts,jsx,tsx,mdx}",
    // HeroUI theme classes — match both hoisted and npm-nested install layouts.
    "./node_modules/@heroui/theme/dist/**/*.{js,ts,jsx,tsx}",
    "./node_modules/@heroui/react/node_modules/@heroui/theme/dist/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        // Use the app's sans for everything; HeroUI's own components inherit it.
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
    },
  },
  darkMode: "class",
  // Stock HeroUI light theme (default blue primary, neutral surfaces).
  plugins: [heroui() as unknown as TWPlugin],
};

export default config;
