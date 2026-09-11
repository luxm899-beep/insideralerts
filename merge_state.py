#!/usr/bin/env python3
"""Merge the local seen.json with whatever is already on the remote branch."""
import json

def load(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

local = load("seen.json")
remote = load("remote.json")

merged = {}
for key in ("sec", "house", "senate"):
    combined = list(remote.get(key, [])) + list(local.get(key, []))
    merged[key] = list(dict.fromkeys(combined))[-8000:]

with open("seen.json", "w") as f:
    json.dump(merged, f, indent=1)

print("merged state:", {k: len(v) for k, v in merged.items()})
