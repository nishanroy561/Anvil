import type { Config } from "tailwindcss";

/**
 * Anvil "iron & ember" tokens. The dashboard is dark-only by design — it is an
 * observability instrument, not a marketing page. Ember is rationed: primary
 * actions, active nav, and the hot span in the waterfall. Span-type colors are
 * the functional palette and appear only on data.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        iron: {
          950: "#0C0E12", // page
          900: "#12151C", // surface
          850: "#181D26", // raised surface / hover
          800: "#1F2530", // active
        },
        line: {
          DEFAULT: "#222835",
          strong: "#2E3646",
        },
        ink: {
          DEFAULT: "#E7EBF2",
          muted: "#8B95A7",
          faint: "#5C6678",
        },
        ember: {
          DEFAULT: "#F4762B",
          bright: "#FF8B42",
          dim: "#B85A22",
        },
        status: {
          ok: "#3ECF8E",
          error: "#F06A5D",
          running: "#4CA9FF",
          abandoned: "#8B95A7",
        },
        span: {
          llm: "#A78BFA",
          retrieval: "#38BDF8",
          tool: "#FBBF24",
          embedding: "#34D399",
          chain: "#7D8CA3",
          custom: "#E879B9",
        },
      },
      fontFamily: {
        display: ["var(--font-display)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", { lineHeight: "1rem" }], // 11px — labels, ticks
      },
      boxShadow: {
        ember: "0 0 12px 0 rgba(244, 118, 43, 0.35)",
        panel: "0 8px 32px rgba(0, 0, 0, 0.45)",
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
