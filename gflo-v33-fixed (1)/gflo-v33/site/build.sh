#!/bin/sh
# Reassemble gflo.html from src/ sections (v33)
cat src/01-head.html src/015-brand.html src/02-art-data.html src/025-realdata.html \
    src/026-api.html src/03-engine.html src/035-photos.html src/04-shell.html \
    src/05-home.html src/055-home-min.html src/06-catalog-product.html \
    src/07-ai-commerce.html src/08-dash-boot.html > gflo.html
echo "gflo.html rebuilt ($(wc -c < gflo.html) bytes)"
