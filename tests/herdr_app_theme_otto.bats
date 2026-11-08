#!/usr/bin/env bats
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/palettes/*.toml tests/fixtures/otto-brand-tokens.css

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

@test "otto palette hexes match the brand tokens" {
  python3 - "$REPO_ROOT" <<'PY'
import re
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
palette = tomllib.loads(
    (root / "ai/herdr/app/palettes/otto.toml").read_text()
)["palette"]
css = (root / "tests/fixtures/otto-brand-tokens.css").read_text()


def block_tokens(css_text: str, selector: str) -> dict[str, str]:
    match = re.search(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", css_text)
    if match is None:
        raise SystemExit(f"missing CSS block {selector}")
    return dict(re.findall(r"(--ow-[\w-]+):\s*(#[0-9a-fA-F]+)", match.group(1)))


root_tokens = block_tokens(css, ":root")
dark_tokens = block_tokens(css, ".dark")
failed = 0
for name, hex_value in palette.items():
    if "_derived" in name:
        continue
    token = "--ow-" + name.replace("_", "-")
    if name.endswith("_light"):
        token = "--ow-" + name[: -len("_light")].replace("_", "-")
        got = root_tokens.get(token)
    elif name.endswith("_dark"):
        token = "--ow-" + name[: -len("_dark")].replace("_", "-")
        got = dark_tokens.get(token)
    else:
        got = root_tokens.get(token)
    if got != hex_value:
        print(f"{name}: palette={hex_value} css={got}")
        failed = 1
sys.exit(failed)
PY
}
