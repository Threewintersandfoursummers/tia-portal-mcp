#!/usr/bin/env python3
# tia_mcp_server.py - MCP stdio server bridging WorkBuddy to TIA Portal V19 Openness.
# Pure stdlib (no pip deps). Launches tia_host.ps1 as a persistent Openness bridge.

import sys
import os
import json
import subprocess
import threading
import queue

PS_EXE = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
HOST_PS1 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tia_host.ps1")

TOOLS = [
    {
        "name": "tia_init",
        "description": "Start TIA Portal V19 in headless Openness mode (WithoutUserInterface).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tia_open_project",
        "description": "Open or create a TIA Portal project. path = project folder or .ap19 archive. create_if_missing = bool.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "create_if_missing": {"type": "boolean"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "tia_list_devices",
        "description": "List devices/PLCs in the currently open project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tia_list_blocks",
        "description": "List program blocks (OB/FB/FC/DB) of a device.",
        "inputSchema": {
            "type": "object",
            "properties": {"device": {"type": "string"}},
            "required": ["device"],
        },
    },
    {
        "name": "tia_export_block",
        "description": "Export a block to a file. out_path extension chooses format: .xml/.scl/.awl.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device": {"type": "string"},
                "block": {"type": "string"},
                "out_path": {"type": "string"},
            },
            "required": ["device", "block", "out_path"],
        },
    },
    {
        "name": "tia_compile",
        "description": "Compile a device or the whole project. target = device name or 'project'.",
        "inputSchema": {
            "type": "object",
            "properties": {"target": {"type": "string"}},
        },
    },
    {
        "name": "tia_project_info",
        "description": "Show info about the open project (name, path, device count).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tia_close",
        "description": "Close the open project. save = bool.",
        "inputSchema": {
            "type": "object",
            "properties": {"save": {"type": "boolean"}},
        },
    },
    {
        "name": "tia_status",
        "description": "Show host/TIA/project status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

HOST = None
HOST_LOCK = threading.Lock()


class TiaHost:
    def __init__(self, timeout=60):
        self.proc = subprocess.Popen(
            [PS_EXE, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-File", HOST_PS1],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
        ready = self.proc.stdout.readline()
        if not ready:
            err = ""
            try:
                err = self.proc.stderr.read(2000)
            except Exception:
                pass
            raise RuntimeError("Host exited early. stderr: " + err)
        try:
            obj = json.loads(ready)
            if not obj.get("ready"):
                raise RuntimeError("Host not ready: " + ready)
        except Exception as e:
            raise RuntimeError("Bad ready line from host: " + ready + " (" + str(e) + ")")

    def request(self, method, params=None, timeout=300):
        msg = {"id": 1, "method": method, "params": params or {}}
        line = json.dumps(msg, ensure_ascii=False)
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except Exception as e:
            raise RuntimeError("Failed to send to host: " + str(e))
        q = queue.Queue()

        def reader():
            try:
                q.put(self.proc.stdout.readline())
            except Exception:
                q.put(None)

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            try:
                self.proc.kill()
            except Exception:
                pass
            raise RuntimeError("Host timeout after %ds (TIA launch too slow or failed)" % timeout)
        raw = q.get()
        if not raw:
            raise RuntimeError("No response from host (process died)")
        try:
            return json.loads(raw)
        except Exception as e:
            raise RuntimeError("Bad JSON from host: " + raw[:200] + " (" + str(e) + ")")


def get_host():
    global HOST
    with HOST_LOCK:
        if HOST is None or HOST.proc.poll() is not None:
            HOST = TiaHost()
        return HOST


def dispatch(name, args):
    mapping = {
        "tia_init": ("init", {}),
        "tia_open_project": ("open_project", {
            "path": args.get("path"),
            "create_if_missing": bool(args.get("create_if_missing", False)),
        }),
        "tia_list_devices": ("list_devices", {}),
        "tia_list_blocks": ("list_blocks", {"device": args.get("device")}),
        "tia_export_block": ("export_block", {
            "device": args.get("device"),
            "block": args.get("block"),
            "out_path": args.get("out_path"),
        }),
        "tia_compile": ("compile", {"target": args.get("target")}),
        "tia_project_info": ("project_info", {}),
        "tia_close": ("close", {"save": bool(args.get("save", False))}),
        "tia_status": ("status", {}),
    }
    if name not in mapping:
        return {"content": [{"type": "text", "text": "Unknown tool: " + name}], "isError": True}
    method, params = mapping[name]
    try:
        host = get_host()
        res = host.request(method, params)
    except Exception as e:
        return {"content": [{"type": "text", "text": "Host error: " + str(e)}], "isError": True}
    if not res.get("ok"):
        return {"content": [{"type": "text", "text": "Error: " + str(res.get("error"))}], "isError": True}
    text = json.dumps(res.get("result"), ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}]}


FRAMING = {"mode": None}  # "newline" (MCP stdio standard) or "lsp" (Content-Length)


def send(obj):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if FRAMING["mode"] == "lsp":
        sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(data))
        sys.stdout.buffer.write(data)
    else:
        sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def recv():
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            return None
        line = raw.decode("utf-8", "replace").strip()
        if line == "":
            continue
        if line.lower().startswith("content-length:"):
            # LSP-style framing (auto-detected fallback)
            FRAMING["mode"] = "lsp"
            clen = int(line.split(":", 1)[1].strip())
            while True:
                h = sys.stdin.buffer.readline()
                if not h:
                    return None
                if h.strip() == b"":
                    break
            body = sys.stdin.buffer.read(clen)
            return json.loads(body.decode("utf-8", "replace"))
        # Newline-delimited JSON (official MCP stdio transport)
        FRAMING["mode"] = "newline"
        return json.loads(line)


def main():
    sys.stderr.write("tia-portal-mcp: stdio ready\n")
    sys.stderr.flush()
    while True:
        msg = recv()
        if msg is None:
            break
        m = msg.get("method")
        mid = msg.get("id")
        if m == "initialize":
            send({
                "jsonrpc": "2.0", "id": mid, "result": {
                    "protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "tia-portal-mcp", "version": "0.1.0"},
                },
            })
        elif m in ("notifications/initialized", "initialized", "ns/tokens/updated"):
            continue
        elif m == "ping":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif m == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif m == "tools/call":
            params = msg.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            send({"jsonrpc": "2.0", "id": mid, "result": dispatch(name, args)})
        else:
            if mid is not None:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32601, "message": "method not found: " + str(m)}})


if __name__ == "__main__":
    main()
