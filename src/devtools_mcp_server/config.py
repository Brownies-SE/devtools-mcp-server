import argparse
import re
from pathlib import Path

from .models import StartupConfig

__all__ = ["StartupConfig", "parse_args"]


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid startup arguments. Use --help for supported options.\n")


def parse_args(arguments: list[str] | None = None) -> StartupConfig:
    parser = _Parser(
        description="Local read-only DevTools MCP server over stdio.", allow_abbrev=False
    )
    parser.add_argument(
        "--exclude-secret-path", action="append", default=[], metavar="ABSOLUTE_PATH"
    )
    parser.add_argument("--allow-health-path", action="append", default=[], metavar="PATH")
    parser.add_argument("--docker-endpoint", metavar="LOCAL_ENDPOINT")
    values = parser.parse_args(arguments)
    if len(values.exclude_secret_path) > 100 or len(values.allow_health_path) > 100:
        parser.error("Too many policy extensions.")
    for value in values.exclude_secret_path:
        if len(value) > 4096 or "\0" in value or not Path(value).is_absolute():
            parser.error("Secret exclusions must be absolute paths.")
    for value in values.allow_health_path:
        if (
            not re.fullmatch(r"/(?:[A-Za-z0-9._~-]+/?)*", value)
            or ".." in value.split("/")
            or len(value) > 4096
        ):
            parser.error("Health paths must be literal URL paths.")
    endpoint = values.docker_endpoint
    if endpoint is not None and (
        len(endpoint) > 4096
        or any(ord(char) < 32 for char in endpoint)
        or any(char in endpoint for char in "%?#")
        or not (
            endpoint.startswith("unix:///")
            and not endpoint.startswith("unix:////")
            or re.fullmatch(r"npipe:////\./pipe/[A-Za-z0-9_.-]{1,256}", endpoint)
        )
    ):
        parser.error("Docker endpoints must be local socket transports.")
    return StartupConfig(
        tuple(values.exclude_secret_path), tuple(values.allow_health_path), endpoint
    )
