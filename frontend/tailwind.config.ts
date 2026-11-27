import type { Config } from "tailwindcss";
import { BRAND_COLOURS, TEXT_SIZES } from "./lib/assistant/tokens";

const brand = Object.fromEntries(
  Object.keys(BRAND_COLOURS).map((name) => [name, `rgb(var(--brand-${name}) / <alpha-value>)`]),
);

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./standins/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: { brand },
      fontSize: TEXT_SIZES as unknown as NonNullable<Config["theme"]>["fontSize"],
      fontFamily: {
        // The system's own fonts: nothing is loaded from another site.
        sans: [
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      borderRadius: { panel: "10px", control: "6px" },
      spacing: { panel: "18px", control: "32px", "control-sm": "28px" },
    },
  },
  plugins: [],
};

export default config;
