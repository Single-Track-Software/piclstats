#!/usr/bin/env bash
# Rebuild the committed Tailwind stylesheet after changing any template's classes.
#
#   scripts/build_css.sh          # writes src/piclstats/web/static/app.css
#
# The stylesheet is committed so the container needs no Node. tests/test_css_build.py
# fails when a class used in the templates is missing from the built file, which is
# the signal to run this and commit the result. Needs npx (Node >= 18).
set -euo pipefail
cd "$(dirname "$0")/.."
npx --yes tailwindcss@3.4.17 -c tailwind.config.js \
    -i src/piclstats/web/static/input.css -o src/piclstats/web/static/app.css --minify
