"""A temporary public URL for a local port, for a platform that must call back into the rig
(ElevenLabs' custom LLM). It runs `ngrok http <port>` for the length of a run and reads the
public address from ngrok's local API."""

import subprocess
import time
from typing import Self

import httpx

API = "http://127.0.0.1:{port}/api/tunnels"


def public_url(tunnels: dict, port: int) -> str | None:
    for tunnel in tunnels.get("tunnels", []):
        addr = str(tunnel.get("config", {}).get("addr", ""))
        if tunnel.get("public_url", "").startswith("https://") and addr.endswith(f":{port}"):
            return tunnel["public_url"]
    return None


class Tunnel:
    def __init__(self, port: int, api_ports=(4040, 4041, 4042)):
        self.port = port
        self.api_ports = api_ports
        self.process: subprocess.Popen | None = None
        self.url = ""

    def __enter__(self) -> Self:
        self.process = subprocess.Popen(
            ["ngrok", "http", str(self.port), "--log", "stdout"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not self.url:
            if self.process.poll() is not None:
                raise RuntimeError("ngrok exited; is another ngrok session already running?")
            for api_port in self.api_ports:
                try:
                    found = public_url(
                        httpx.get(API.format(port=api_port), timeout=1).json(), self.port
                    )
                except (httpx.HTTPError, ValueError):
                    continue
                if found:
                    self.url = found
                    break
            time.sleep(0.3)
        if not self.url:
            self.close()
            raise RuntimeError("ngrok gave no public URL within 20 s")
        return self

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)

    def __exit__(self, *exc) -> None:
        self.close()
