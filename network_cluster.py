"""Shared ComfyUI cluster helpers for Workflows Q-builder."""

import json
import urllib.error
import urllib.request


def parse_servers(primary, raw=None):
    values = []
    if raw:
        values.extend(part.strip().rstrip("/") for part in raw.split(","))
    if not values and primary:
        values.append(primary.strip().rstrip("/"))
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result or ["http://127.0.0.1:8188"]


def queue_depth(server, timeout=3):
    try:
        with urllib.request.urlopen(f"{server.rstrip('/')}/queue", timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return len(data.get("queue_running") or []) + len(data.get("queue_pending") or [])
    except (OSError, ValueError, urllib.error.URLError):
        return None


def initial_loads(servers):
    loads = {}
    for server in servers:
        depth = queue_depth(server)
        if depth is not None:
            loads[server] = depth
    if not loads:
        raise RuntimeError("Aucun serveur ComfyUI du cluster ne repond.")
    return loads


def select_server(loads):
    server = min(loads, key=lambda item: (loads[item], item))
    loads[server] += 1
    return server