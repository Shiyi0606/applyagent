#!/usr/bin/env python3
"""Materialize optional private settings or the public generic discovery fallback."""
import argparse
import json
import os
from pathlib import Path
import sys

from job_search import validate_config


def load_config(raw: str, fallback: Path) -> tuple[dict, str]:
    config = json.loads(raw) if raw else json.loads(fallback.read_text(encoding="utf-8"))
    validate_config(config)
    return config, "private environment configuration" if raw else "public generic fallback"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fallback", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        config, mode = load_config(os.environ.get("JOB_SEARCH_CONFIG_JSON", ""), args.fallback)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(config, handle, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as error:
        print(f"Discovery configuration error ({type(error).__name__}); check JSON schema and file access. Configuration values were not logged.", file=sys.stderr)
        return 2
    print(f"Discovery configuration: {mode}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
