#!/usr/bin/env bash
# Stamps the release version into the file given as $1.
set -euo pipefail

target="$1"
# -i.bak with no space between flag and suffix is the one in-place spelling
# both BSD and GNU sed accept.
sed -i.bak "s/PLACEHOLDER/1.0.0/" "$target"
rm -f "$target.bak"
