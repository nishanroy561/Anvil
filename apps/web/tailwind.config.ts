import type { Config } from "tailwindcss";

/**
 * Anvil neutral tokens. Light, paper-like surfaces with near-black ink and a
 * single blue accent, rationed to primary emphasis: active nav, the hot span,
 * focus. Span-type colors are the functional palette and appear only on data.
 * Token names are roles, not lightnesses: iron-950 is "page", iron-900 is
 * "surface", iron-850 is "raised/hover", iron-800 is "active".
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        iron: {
          950: "#F6F6F5", // page
          900: "#FFFFFF", // surface
          850: "#EFEFED", // raised surface / hover
          800: "#E8E8E6", // active
        },
        line: {
          DEFAULT: "#E5E5E3",
          strong: "#D6D6D5",
        },
        ink: {
          DEFAULT: "#131316",
          muted: "#545464",
          faint: "#72727F", // 4.5:1 on white — timestamps and ticks stay AA
        },
        // Historically the orange brand accent; now the single blue UI accent.
        ember: {
          DEFAULT: "#3B76FF",
          bright: "#2563EB", // hover emphasis
          dim: "#A8C2FF", // tint
        },
        status: {
          ok: "#039855",
          error: "#D92D20",
          running: "#1570EF",
          abandoned: "#72727F",
          // "This number is not what it looks like" — distinct from error,
          // which means something broke. Amber-700 from the same family as the
          // others; 5.9:1 on the page surface.
          warn: "#B54708",
        },
        span: {
          llm: "#7C3AED",
          retrieval: "#1570EF",
          tool: "#DC6803",
          embedding: "#039855",
          chain: "#64748B",
          custom: "#DD2590",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        display: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }], // 11px — labels, ticks
      },
      boxShadow: {
        ember: "0 0 0 3px rgba(59, 118, 255, 0.18)",
        panel: "0 1px 2px rgba(19, 19, 22, 0.05), 0 12px 32px rgba(19, 19, 22, 0.08)",
      },
      animation: {
        "pulse-soft": "pulse-soft 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
      keyframes: {
        "pulse-soft": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.45" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
