"""Minimal JSON-over-stdio client for the TIA Portal Openness host.

Keeps one tia_host.ps1 process (and therefore one TIA Portal instance) alive
across many calls, which is what makes iterative block-import work practical.
"""
import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.join(HERE, "tia_host.ps1")


class TiaClient:
    def __init__(self, host=HOST):
        self._id = 0
        self._lock = threading.Lock()
        self.proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NoLogo",
             "-ExecutionPolicy", "Bypass", "-File", host],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # first line is {"ready":true}
        line = self.proc.stdout.readline()
        if not line:
            err = self.proc.stderr.read().decode("utf-8", "replace")
            raise RuntimeError("host died before ready:\n" + err)
        self.ready = json.loads(line.decode("utf-8", "replace"))
        # drain stderr in background so the pipe cannot fill up and deadlock
        self._stderr = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self):
        for raw in iter(self.proc.stderr.readline, b""):
            self._stderr.append(raw.decode("utf-8", "replace"))

    def call(self, method, **params):
        with self._lock:
            self._id += 1
            mid = self._id
            req = {"id": mid, "method": method, "params": params}
            payload = json.dumps(req, ensure_ascii=False).encode("utf-8") + b"\n"
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
            if not line:
                err = "".join(self._stderr)
                raise RuntimeError(f"host died during {method}:\n{err}")
            resp = json.loads(line.decode("utf-8", "replace"))
            if not resp.get("ok"):
                raise RuntimeError(f"{method} failed: {resp.get('error')}")
            return resp.get("result")

    # ---- convenience wrappers -------------------------------------------------
    def init(self, ui=False):
        return self.call("init", ui=ui)

    def create_project(self, path):
        return self.call("create_project", path=path)

    def save(self):
        return self.call("save")

    def add_device(self, name, order_numbers):
        return self.call("add_device", name=name, order_numbers=order_numbers)

    def list_devices(self):
        return self.call("list_devices")

    def list_blocks(self, device):
        return self.call("list_blocks", device=device)

    def list_tables(self, device):
        return self.call("list_tables", device=device)

    def add_tags(self, device, tags, table=None):
        return self.call("add_tags", device=device, tags=tags, table=table)

    def import_blocks(self, device, files):
        return self.call("import_blocks", device=device, files=files)

    def export_block(self, device, block, out_path):
        return self.call("export_block", device=device, block=block, out_path=out_path)

    def delete_block(self, device, block):
        return self.call("delete_block", device=device, block=block)

    def compile(self, target="project"):
        return self.call("compile", target=target)

    def info(self):
        return self.call("project_info")

    def shutdown(self):
        try:
            self.call("shutdown")
        except Exception:
            pass
        try:
            self.proc.wait(timeout=120)
        except Exception:
            self.proc.kill()

    def stderr_text(self):
        return "".join(self._stderr)


if __name__ == "__main__":
    c = TiaClient()
    print("ready:", c.ready)
    print("info  :", c.init())
    print("proj  :", c.create_project(r"C:\Users\Administrator\Desktop\TL_Probe\TL_Probe"))
    print("dev   :", c.add_device("PLC_1", [
        "OrderNumber:6ES7 214-1AG40-0XB0/V4.6",
        "OrderNumber:6ES7 214-1AG40-0XB0/V4.5",
        "OrderNumber:6ES7 214-1AG40-0XB0/V4.4",
        "OrderNumber:6ES7 214-1AG40-0XB0/V4.3",
        "OrderNumber:6ES7 214-1AG40-0XB0/V4.2",
        "OrderNumber:6ES7 214-1AG40-0XB0/V4.1",
        "OrderNumber:6ES7 214-1AG40-0XB0/V4.0",
    ]))
    print("devs  :", c.list_devices())
    print("info  :", c.info())
    print("save  :", c.save())
    c.shutdown()
