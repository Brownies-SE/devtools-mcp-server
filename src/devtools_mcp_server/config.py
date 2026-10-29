import argparse
from pathlib import Path

from .models import StartupConfig


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid startup arguments. Use --help for supported options.\n")


def parse_args(arguments: list[str] | None = None) -> StartupConfig:
    parser = _Parser(description="Local read-only Git MCP server over stdio.", allow_abbrev=False)
    parser.add_argument(
        "--exclude-secret-path", action="append", default=[], metavar="ABSOLUTE_PATH"
    )
    values = parser.parse_args(arguments)
    if len(values.exclude_secret_path) > 100:
        parser.error("Too many exclusions.")
    for value in values.exclude_secret_path:
        if len(value) > 4096 or "\0" in value or not Path(value).is_absolute():
            parser.error("Secret exclusions must be absolute paths.")
    return StartupConfig(tuple(values.exclude_secret_path))
