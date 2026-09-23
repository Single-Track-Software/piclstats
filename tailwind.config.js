/** Tailwind build config — see scripts/build_css.sh. */
module.exports = {
  content: [
    "./src/piclstats/web/templates/**/*.html",
    "./src/piclstats/web/*.py",
  ],
  // Classes assembled in Jinja expressions (md:grid-cols-{{ n }}) that the
  // scanner cannot see.
  safelist: ["md:grid-cols-1", "md:grid-cols-2", "md:grid-cols-3", "md:grid-cols-4"],
  theme: {
    extend: {
      colors: {
        // Brand navy (Collateral/ — sampled from the 2026 logo set). 800 is
        // the nav and hero; 700 is link/heading ink on white; 500 is the
        // chart series colour; 200/100/50 are tints for muted text and hovers.
        picl: {
          50: "#f0f4fa", 100: "#dce4f1", 200: "#b7c5de",
          500: "#2a5a99", 600: "#1e4478", 700: "#14305c",
          800: "#0b1f3d", 900: "#06162e",
        },
        // Brand gold — accents only: active nav, hero rules, badges.
        gold: {
          50: "#fcf7e8", 100: "#f8eed3", 300: "#e9c76f",
          400: "#e0b24e", 500: "#d4a034", 600: "#b8862a", 700: "#946a1f",
        },
      },
    },
  },
};
