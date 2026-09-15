#!/usr/bin/env python3
"""Validate a local-contactome config and emit values for the shell runner."""

import json
import sys


def fail(message):
    raise ValueError(message)


def string(config, name, default=None, required=False):
    value = config.get(name, default)
    if value is None and not required:
        return value
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        fail(f"{name} must be a non-empty single-line string")
    return value


def positive_triplet(value, name):
    if not isinstance(value, str):
        fail(f"{name} must be a comma-separated string of three positive integers")
    parts = value.split(",")
    if len(parts) != 3 or any(not part.isdigit() or int(part) < 1 for part in parts):
        fail(f"{name} must be a comma-separated string of three positive integers")
    return value


def read_config(config_path, default_graph_id):
    with open(config_path, encoding="utf-8") as config_file:
        config = json.load(config_file)
    if not isinstance(config, dict):
        fail("configuration must be a JSON object")

    segmentation_uri = string(config, "segmentation_uri", required=True)
    graph_id = string(config, "graph_id", default_graph_id)
    output_directory = string(config, "output_directory", f"contactome-output/{graph_id}")
    mip = config.get("mip", "72,72,84")
    if isinstance(mip, int) and mip >= 0:
        mip = str(mip)
    elif not isinstance(mip, str) or not mip:
        fail("mip must be a non-negative integer or a non-empty resolution string")
    block_size = positive_triplet(config.get("block_size", "64,64,32"), "block_size")

    bounds = []
    for name in ("z_start", "z_end", "enqueue_limit"):
        value = config.get(name)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            fail(f"{name} must be a non-negative integer or null")
        bounds.append("" if value is None else str(value))
    z_start, z_end, enqueue_limit = bounds
    if z_start and z_end and int(z_start) > int(z_end):
        fail("z_start must not be greater than z_end")

    return (
        segmentation_uri,
        graph_id,
        output_directory,
        mip,
        block_size,
        z_start,
        z_end,
        enqueue_limit,
    )


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} CONFIG_PATH DEFAULT_GRAPH_ID", file=sys.stderr)
        return 2
    try:
        values = read_config(sys.argv[1], sys.argv[2])
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"error: invalid configuration: {error}", file=sys.stderr)
        return 1

    print("\n".join(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
