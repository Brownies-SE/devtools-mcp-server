import os
import socket
import sys
import time
from datetime import UTC, datetime
from typing import Literal

import psutil
from pydantic import Field

from ..models import InspectionError, PageArgs, Reply, StrictModel, Warning
from ..pagination import page


class ProcessesListArgs(PageArgs):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    pid: int | None = Field(default=None, ge=1, le=2147483647)


class PortsListArgs(PageArgs):
    port: int | None = Field(default=None, ge=1, le=65535)
    protocol: Literal["tcp", "udp"] | None = None
    pid: int | None = Field(default=None, ge=1, le=2147483647)


class ProcessRow(StrictModel):
    pid: int
    created_at: str | None
    ppid: int | None = None
    name: str | None = None
    state: str | None = None
    cpu_percent: float | None = None
    memory_rss_bytes: int | None = None


class ProcessesData(StrictModel):
    processes: list[ProcessRow]
    cpu_sample_seconds: float = 0.1


class SocketRow(StrictModel):
    address: str
    family: Literal["ipv4", "ipv6"]
    port: int
    protocol: Literal["tcp", "udp"]
    state: Literal["listen", "bound"]
    pid: int | None
    name: str | None


class PortsData(StrictModel):
    sockets: list[SocketRow]


def _warn(
    warnings, code="ACCESS_DENIED", message="Some process or socket observations are unavailable."
):
    if len(warnings) < 10 and not any(
        item.code == code and item.message == message for item in warnings
    ):
        warnings.append(Warning(code=code, message=message))


def list_processes(args: ProcessesListArgs, ctx) -> Reply[ProcessesData]:
    warnings, selected = [], []
    for process in psutil.process_iter():
        try:
            ctx.budget.visit_entry()
            if args.pid is not None and process.pid != args.pid:
                continue
            created = process.create_time()
            name = process.name()
            if args.name is not None and args.name.casefold() not in name.casefold():
                continue
            row = ProcessRow(
                pid=process.pid,
                created_at=datetime.fromtimestamp(created, UTC).isoformat(),
                name=name,
            )
            selected.append((process, created, row))
            row.ppid, row.state = process.ppid(), process.status()
            process.cpu_percent(interval=None)
        except psutil.NoSuchProcess, psutil.AccessDenied:
            _warn(warnings)
        except InspectionError:
            _warn(warnings, "OUTPUT_LIMIT", "Process enumeration limit reached; narrow the query.")
            break
    if selected:
        time.sleep(0.1)
    for process, created, row in selected:
        try:
            if psutil.Process(process.pid).create_time() != created:
                raise psutil.NoSuchProcess(process.pid)
            row.cpu_percent = float(process.cpu_percent(interval=None))
            row.memory_rss_bytes = process.memory_info().rss
        except psutil.NoSuchProcess, psutil.AccessDenied:
            row.cpu_percent = row.memory_rss_bytes = None
            _warn(warnings)
    rows = sorted((row for _, _, row in selected), key=lambda row: row.pid)
    rows, cursor = page(rows, args, "processes_list", args.model_dump(exclude={"limit", "cursor"}))
    capped = any(item.code == "OUTPUT_LIMIT" for item in warnings)
    return Reply[ProcessesData](
        data=ProcessesData(processes=rows),
        status="partial" if warnings else "ok",
        warnings=warnings,
        truncated=capped,
        next_cursor=None if capped else cursor,
    )


def _connections(args, ctx, warnings):
    try:
        rows = psutil.net_connections(kind="inet")
        if sys.platform.startswith("linux") and os.geteuid() != 0:
            _warn(warnings, message="The OS may omit sockets owned by other users.")
        yield from ((row, row.pid) for row in rows)
    except psutil.AccessDenied:
        _warn(
            warnings,
            message="System-wide sockets are restricted; only accessible process sockets are shown.",
        )
        for process in psutil.process_iter():
            ctx.budget.visit_entry()
            if args.pid is not None and process.pid != args.pid:
                continue
            try:
                yield from ((row, process.pid) for row in process.net_connections(kind="inet"))
            except psutil.NoSuchProcess, psutil.AccessDenied:
                _warn(warnings)


def list_ports(args: PortsListArgs, ctx) -> Reply[PortsData]:
    warnings, rows = [], []
    try:
        for connection, pid in _connections(args, ctx, warnings):
            ctx.budget.visit_entry()
            if not connection.laddr or connection.family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            protocol = "tcp" if connection.type == socket.SOCK_STREAM else "udp"
            if protocol == "tcp" and connection.status != psutil.CONN_LISTEN:
                continue
            if (
                args.protocol is not None
                and protocol != args.protocol
                or args.port is not None
                and connection.laddr.port != args.port
                or args.pid is not None
                and pid != args.pid
            ):
                continue
            name = None
            if pid is not None:
                try:
                    name = psutil.Process(pid).name()
                except psutil.NoSuchProcess, psutil.AccessDenied:
                    _warn(warnings)
            else:
                _warn(warnings, message="Some socket owners are unknown.")
            rows.append(
                SocketRow(
                    address=connection.laddr.ip,
                    family="ipv4" if connection.family == socket.AF_INET else "ipv6",
                    port=connection.laddr.port,
                    protocol=protocol,
                    state="listen" if protocol == "tcp" else "bound",
                    pid=pid,
                    name=name,
                )
            )
    except InspectionError:
        _warn(warnings, "OUTPUT_LIMIT", "Socket enumeration limit reached; narrow the query.")
    rows.sort(key=lambda row: (row.address, row.family, row.protocol, row.port, row.pid or -1))
    rows, cursor = page(rows, args, "ports_list", args.model_dump(exclude={"limit", "cursor"}))
    capped = any(item.code == "OUTPUT_LIMIT" for item in warnings)
    return Reply[PortsData](
        data=PortsData(sockets=rows),
        status="partial" if warnings else "ok",
        warnings=warnings,
        truncated=capped,
        next_cursor=None if capped else cursor,
    )
