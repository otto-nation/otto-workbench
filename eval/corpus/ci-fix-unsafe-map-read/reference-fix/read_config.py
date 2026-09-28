#!/usr/bin/env python3
"""Print the name field from the JSON object in the given file."""
import json
import sys
from pathlib import Path


def main(path: str) -> None:
    data = json.loads(Path(path).read_text())
    name = data.get("name")
    if name is None:
        print("error: missing key 'name'", file=sys.stderr)
        sys.exit(1)
    print(name)


if __name__ == "__main__":
    main(sys.argv[1])
