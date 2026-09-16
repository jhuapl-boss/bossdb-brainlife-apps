#!/usr/bin/env python3
"""Validate a local-contactome config and emit values for the shell runner."""

import json
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
import urllib.error
import urllib.request
from urllib.parse import quote, unquote, urlsplit

DEFAULT_BOSSDB_API = "https://api.bossdb.io/v1"
_S3_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


@dataclass(frozen=True)
class BossDBChannel:
    uri: str
    collection: str
    experiment: str
    channel: str
    cloudpath: str


def fail(message):
    raise ValueError(message)


def string(config, name, default=None, required=False):
    value = config.get(name, default)
    if value is None and not required:
        return value
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        fail(f"{name} must be a non-empty single-line string")
    return value.strip()


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

    if "segmentation_uri" in config:
        fail("segmentation_uri is no longer supported; use input in brainlife instead")
    input_uri = string(config, "channel", required=True)
    segmentation_channel = resolve_channel(input_uri).cloudpath
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
        segmentation_channel,
        graph_id,
        output_directory,
        mip,
        block_size,
        z_start,
        z_end,
        enqueue_limit,
    )

def fetch_json(url: str, timeout: int = 30):
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "brainlife-nuclei/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ValueError("BossDB channel was not found or is not public") from error
        raise RuntimeError(
            f"BossDB metadata request failed with HTTP {error.code}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError("BossDB metadata service could not be reached") from error


def _precomputed_cloudpath(metadata) -> str:
    if metadata.get("storage_type") != "cloudvol":
        raise ValueError("BossDB channel is not stored as a precomputed CloudVolume")
    bucket = metadata.get("bucket")
    cv_path = metadata.get("cv_path")
    if not isinstance(bucket, str) or not _S3_BUCKET.fullmatch(bucket):
        raise ValueError("BossDB metadata did not provide a valid S3 bucket")
    if not isinstance(cv_path, str) or not cv_path.strip("/"):
        raise ValueError("BossDB metadata did not provide a precomputed path")
    cv_path = cv_path.strip("/")
    if any(part in ("", ".", "..") for part in PurePosixPath(cv_path).parts):
        raise ValueError("BossDB metadata provided an invalid precomputed path")
    if "://" in cv_path:
        raise ValueError("BossDB metadata provided an invalid precomputed path")
    return f"s3://{bucket}/{cv_path}"


def parse_bossdb_uri(uri: str) -> tuple[str, str, str]:
    parsed = urlsplit(uri)
    parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    collection = unquote(parsed.netloc)
    if (
        parsed.scheme.lower() != "bossdb"
        or not collection
        or len(parts) != 2
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "input must have the form bossdb://collection/experiment/channel"
        )
    if any(
        "/" in value or "\\" in value or value in (".", "..")
        for value in (collection, *parts)
    ):
        raise ValueError("BossDB identifiers may not contain path separators")
    return collection, parts[0], parts[1]


def resolve_channel(
    uri: str,
    *,
    api_root = DEFAULT_BOSSDB_API,
    json_fetcher = None,
):
    """Resolve a public BossDB channel URI to its precomputed CloudVolume path."""
    collection, experiment, channel = parse_bossdb_uri(uri)
    resource_url = (
        f"{api_root.rstrip('/')}/collection/{quote(collection, safe='')}"
        f"/experiment/{quote(experiment, safe='')}/channel/{quote(channel, safe='')}"
    )
    metadata = (json_fetcher or fetch_json)(resource_url)
    if not isinstance(metadata, dict):
        raise ValueError("BossDB metadata response was not a JSON object")
    if metadata.get("public") is not True:
        raise ValueError("BossDB channel is not public")
    return BossDBChannel(
        uri=uri,
        collection=collection,
        experiment=experiment,
        channel=channel,
        cloudpath=_precomputed_cloudpath(metadata),
    )


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} CONFIG_PATH DEFAULT_GRAPH_ID", file=sys.stderr)
        return 2
    try:
        values = read_config(sys.argv[1], sys.argv[2])
    except (OSError, json.JSONDecodeError, RuntimeError, ValueError) as error:
        print(f"error: invalid configuration: {error}", file=sys.stderr)
        return 1

    print("\n".join(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
