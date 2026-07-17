/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        // Finyl-DCP brand palette
        canvas: "#F3F4F6",      // primary 60% — off-white
        surface: "#FFFFFF",
        border: "#E5E7EB",      // cool gray
        charcoal: "#1F2937",    // secondary 30% — sidebar/nav & headings
        accent: "#10B981",      // emerald — CTAs / success
        teal: "#0D9488",        // deep teal — secondary accent
      },
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"] },
    },
  },
  plugins: [],
};
