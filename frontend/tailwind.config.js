/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        // --- Finyl-DCP brand palette (ORIGINAL names — kept working, additive) ---
        canvas: "#F6F8FA",      // primary 60% — slightly cooler off-white
        surface: "#FFFFFF",
        surface2: "#FBFCFD",    // subtle raised/alt surface
        border: "#E5E7EB",      // cool gray
        charcoal: "#1F2937",    // secondary 30% — sidebar/nav & headings
        accent: "#10B981",      // emerald — CTAs / success
        teal: "#0D9488",        // deep teal — secondary accent

        // --- Semantic brand ramp (emerald) ---
        brand: {
          50: "#ECFDF5",
          100: "#D1FAE5",
          200: "#A7F3D0",
          300: "#6EE7B7",
          400: "#34D399",
          500: "#10B981",       // = accent
          600: "#059669",
          700: "#047857",
          800: "#065F46",
          900: "#064E3B",
        },

        // --- Ink (charcoal ramp) for headings/body/muted text ---
        ink: {
          900: "#111827",
          600: "#4B5563",
          400: "#9CA3AF",
        },

        // --- Status / semantic accents ---
        info: "#3B82F6",
        warn: "#F59E0B",
        danger: "#EF4444",
      },
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"] },
      boxShadow: {
        // Soft two-layer elevation for cards
        card: "0 1px 2px 0 rgba(16,24,40,0.06), 0 8px 24px -14px rgba(16,24,40,0.18)",
        "card-hover": "0 2px 4px 0 rgba(16,24,40,0.06), 0 16px 32px -14px rgba(16,24,40,0.28)",
        glow: "0 0 0 3px rgba(16,185,129,0.18)",
      },
      borderRadius: {
        xl2: "1rem",
        "2xl": "1rem",
      },
      backgroundImage: {
        "brand-gradient": "linear-gradient(135deg, #10B981 0%, #0D9488 100%)",
        "hero-gradient": "linear-gradient(135deg, #10B981 0%, #0D9488 60%, #0F766E 100%)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "slide-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.2s ease-out both",
        "slide-up": "slide-up 0.2s ease-out both",
        shimmer: "shimmer 1.6s linear infinite",
      },
    },
  },
  plugins: [],
};
