import importlib
import socket
from types import SimpleNamespace

import psutil


def processes():
    assert importlib.util.find_spec("devtools_mcp_server.tools.processes"), (
        "Process/socket inspectors missing"
    )
    return importlib.import_module("devtools_mcp_server.tools.processes")


def test_sockets_only_include_listeners_and_udp_bindings(monkeypatch, inspection_context):
    module = processes()

    def connection(kind, status, port):
        return SimpleNamespace(
            type=kind,
            family=socket.AF_INET,
            status=status,
            laddr=SimpleNamespace(ip="127.0.0.1", port=port),
            pid=None,
        )

    monkeypatch.setattr(
        psutil,
        "net_connections",
        lambda kind: [
            connection(socket.SOCK_STREAM, "LISTEN", 8000),
            connection(socket.SOCK_STREAM, "ESTABLISHED", 8001),
            connection(socket.SOCK_DGRAM, "NONE", 8002),
        ],
    )
    reply = module.list_ports(module.PortsListArgs(), inspection_context)
    assert [(item.port, item.protocol, item.state) for item in reply.data.sockets] == [
        (8000, "tcp", "listen"),
        (8002, "udp", "bound"),
    ]
    assert reply.status == "partial"
    assert all(item.pid is None for item in reply.data.sockets)


def test_permission_failure_not_reported_as_empty_success(monkeypatch, inspection_context):
    module = processes()

    def denied(kind):
        raise psutil.AccessDenied()

    monkeypatch.setattr(psutil, "net_connections", denied)
    monkeypatch.setattr(psutil, "process_iter", lambda: iter([]))
    reply = module.list_ports(module.PortsListArgs(), inspection_context)
    assert reply.status == "partial"
    assert reply.warnings[0].code == "ACCESS_DENIED"


def test_process_batch_samples_once_and_rejects_pid_reuse(monkeypatch, inspection_context):
    module = processes()
    sleeps = []

    class Process:
        pid = 42

        def create_time(self):
            return 1.0

        def name(self):
            return "fixture"

        def ppid(self):
            return 1

        def status(self):
            return "running"

        def cpu_percent(self, interval=None):
            return 150.0

        def memory_info(self):
            return SimpleNamespace(rss=4096)

    class Reused(Process):
        def create_time(self):
            return 2.0

    monkeypatch.setattr(psutil, "process_iter", lambda: iter([Process()]))
    monkeypatch.setattr(psutil, "Process", lambda pid: Reused())
    monkeypatch.setattr(module.time, "sleep", lambda seconds: sleeps.append(seconds))
    reply = module.list_processes(module.ProcessesListArgs(), inspection_context)
    assert sleeps == [0.1]
    assert reply.status == "partial"
    assert reply.data.processes[0].cpu_percent is None
    assert reply.data.processes[0].memory_rss_bytes is None
