import importlib
import os
import socket


def test_native_process_and_owned_listener(inspection_context):
    assert importlib.util.find_spec("devtools_mcp_server.tools.processes"), (
        "Process inspector missing"
    )
    module = importlib.import_module("devtools_mcp_server.tools.processes")
    reply = module.list_processes(module.ProcessesListArgs(pid=os.getpid()), inspection_context)
    assert len(reply.data.processes) == 1
    row = reply.data.processes[0]
    assert row.pid == os.getpid()
    assert row.created_at.endswith("+00:00")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        reply = module.list_ports(module.PortsListArgs(port=port), inspection_context)
        assert any(row.port == port and row.protocol == "tcp" for row in reply.data.sockets)
