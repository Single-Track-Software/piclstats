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
        picl: {
          50: "#eef0fe", 100: "#d8dbfc", 200: "#a9aff8",
          500: "#1020e8", 600: "#0d1bc8", 700: "#0b16a7",
          800: "#091285", 900: "#060d5f",
        },
      },
    },
  },
};
