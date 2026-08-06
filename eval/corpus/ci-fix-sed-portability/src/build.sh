#!/usr/bin/env bash
# Stamps the release version into the file given as $1.
set -euo pipefail

target="$1"
sed -i '' "s/PLACEHOLDER/1.0.0/" "$target"
