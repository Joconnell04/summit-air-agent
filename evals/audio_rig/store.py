"""A durable copy of each simulated call, under `sim/` in the private recordings bucket that real
calls pass through (DECISIONS.md 052, app/recordings.py). The jobs loop only touches the keys it
gave real calls, so files here stay until someone removes them.

The bucket's keys live in the server's .env and the vault, not on the laptop. When they aren't in
this process's environment they are read from the server over ssh at run time and kept in memory;
they are never written to a file or printed.
"""

import subprocess
from pathlib import Path

PREFIX = "sim/"
HOST = "summit-air"
SERVER_ENV = "summit-air/.env"
NAMES = (
    "RECORDING_S3_BUCKET",
    "RECORDING_S3_ENDPOINT",
    "RECORDING_S3_REGION",
    "RECORDING_S3_ACCESS_KEY",
    "RECORDING_S3_SECRET",
)
CONTENT_TYPES = {".flac": "audio/flac", ".wav": "audio/wav", ".json": "application/json"}


def parse_env(text: str) -> dict[str, str]:
    """The bucket's variables from .env text; anything else in it is ignored."""
    found = {}
    for raw in text.splitlines():
        name, sep, value = raw.strip().partition("=")
        if sep and name in NAMES:
            found[name] = value.strip().strip("'\"")
    return found


def bucket_env(env: dict, run=subprocess.run) -> dict[str, str] | None:
    """The bucket's variables: this environment's if it has them, else the server's."""
    if env.get("RECORDING_S3_BUCKET"):
        return {name: env.get(name, "") for name in NAMES}
    try:
        done = run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                HOST,
                f"grep '^RECORDING_S3_' {SERVER_ENV}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    found = parse_env(done.stdout)
    return found if found.get("RECORDING_S3_BUCKET") else None


class SimStore:
    def __init__(self, keys: dict[str, str], client=None):
        self.name = keys["RECORDING_S3_BUCKET"]
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=keys.get("RECORDING_S3_ENDPOINT") or None,
                region_name=keys.get("RECORDING_S3_REGION") or None,
                aws_access_key_id=keys.get("RECORDING_S3_ACCESS_KEY"),
                aws_secret_access_key=keys.get("RECORDING_S3_SECRET"),
            )
        self.client = client
        self.failed = 0

    def put(self, path: Path, key: str) -> bool:
        """Copy one file; a refusal is reported and the run goes on, since the local copy is
        already written."""
        try:
            self.client.upload_file(
                str(path),
                self.name,
                PREFIX + key,
                ExtraArgs={
                    "ContentType": CONTENT_TYPES.get(path.suffix, "application/octet-stream")
                },
            )
            return True
        except Exception as err:  # noqa: BLE001 - boto3 raises several unrelated types
            self.failed += 1
            print(f"durable copy of {key} failed: {type(err).__name__}", flush=True)
            return False


def open_store(env: dict) -> SimStore | None:
    keys = bucket_env(env)
    if keys is None:
        print(f"recordings stay local only: no bucket keys here or on {HOST}", flush=True)
        return None
    return SimStore(keys)
