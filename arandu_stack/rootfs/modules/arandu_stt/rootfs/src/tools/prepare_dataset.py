#!/usr/bin/env python3
"""Convenience CLI. No subcommand means prepare; training is always explicit."""
import sys

from stt_pipeline.cli import main

if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments and arguments[0].startswith("--") and arguments[0] not in ("--help", "-h"):
        arguments.insert(0, "prepare")
    raise SystemExit(main(arguments))
