"""Enable Docker's native cache-capable image store on a fresh ephemeral runner."""

import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


def merged_config(config):
    if not isinstance(config, dict) or not isinstance(config.get("features", {}), dict):
        raise ValueError("Docker daemon configuration and features must be objects")
    return {**config, "features": {**config.get("features", {}), "containerd-snapshotter": True}}


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def enable(config_path=Path("/etc/docker/daemon.json")):
    info = json.loads(command("docker", "info", "--format", "{{json .}}"))
    if any("io.containerd.snapshotter.v1" in row for row in info.get("DriverStatus", []) or []):
        return
    version = tuple(int(part) for part in info["ServerVersion"].split(".")[:2])
    if version < (28, 0):
        raise RuntimeError("The native cache-capable image store requires Docker Engine 28 or later")
    # Restarting/switching stores would hide service containers and pulled images.
    # This opt-in profile must run before job containers are created. Preloaded
    # runner images remain on disk in the old store; nothing is pruned/deleted.
    if command("docker", "container", "ls", "--all", "--quiet"):
        raise RuntimeError("Native image-store setup must run before creating any containers")
    original = config_path.read_bytes() if config_path.exists() else None
    config = merged_config(json.loads(original) if original else {})
    config_path.parent.mkdir(parents=True, exist_ok=True)
    permissions = stat.S_IMODE(config_path.stat().st_mode) if original is not None else 0o644
    with tempfile.NamedTemporaryFile(mode="w", dir=config_path.parent, delete=False) as temp:
        json.dump(config, temp)
        temp_path = Path(temp.name)
    try:
        subprocess.run(["dockerd", "--validate", "--config-file", str(temp_path)], check=True)
        os.chmod(temp_path, permissions)
        os.replace(temp_path, config_path)
        subprocess.run(["systemctl", "restart", "docker"], check=True)
        status = command("docker", "info", "--format", "{{json .DriverStatus}}")
        if "io.containerd.snapshotter.v1" not in status:
            raise RuntimeError("Docker did not activate the containerd image store")
    except Exception:
        if original is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_bytes(original)
            os.chmod(config_path, permissions)
        subprocess.run(["systemctl", "restart", "docker"], check=False)
        raise
    finally:
        temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    enable()
