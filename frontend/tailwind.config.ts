import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        brand: {
          50: "#eef4ff",
          100: "#d9e6ff",
          300: "#a8c0ef",
          400: "#4f7cd6",
          500: "#33588f",
          600: "#274469",
          700: "#1c3350",
          900: "#0f1c30",
        },
        status: {
          good: "#2f7a52",
          info: "#35688c",
          warn: "#a9711f",
          crit: "#a13a2e",
          neutral: "#5b6572",
        },
      },
      fontFamily: {
        sans: ["'Inter'", "system-ui", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
