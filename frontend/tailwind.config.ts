import type { Config } from "tailwindcss";

// Hybrid Editorial design tokens (see ../ACCEPTANCE.md "UI DESIGN TOKENS").
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        paper: "#FAF8F3", // background (paper white)
        ink: "#1A1A1A", // near-black text
        accent: "#8B2E2E", // deep oxblood / ink-red — the ONE accent
        muted: "#6B6660", // secondary text
        hairline: "#E3DED4", // rules / borders
      },
      fontFamily: {
        // Source Serif loaded via next/font -> CSS var; Georgia fallback.
        serif: ["var(--font-serif)", "Georgia", "serif"],
        // IBM Plex Mono via next/font -> CSS var; ui-monospace fallback.
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      lineHeight: {
        body: "1.6", // dense but airy
      },
      maxWidth: {
        editorial: "44rem", // measured reading column
        wide: "72rem",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(2px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 160ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
