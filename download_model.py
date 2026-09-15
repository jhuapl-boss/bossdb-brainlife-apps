#!/usr/bin/env python3
"""Download a public S3 prefix during the container build."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

_CHECKPOINT_SUFFIXES = (".ckpt", ".pth", ".pt")


def open_url(request: urllib.request.Request, timeout: int):
    """Open a public artifact with bounded retries for transient build failures."""
    for attempt in range(4):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == 3:
                raise
        except urllib.error.URLError:
            if attempt == 3:
                raise
        time.sleep(2**attempt)
    raise AssertionError("unreachable")


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme != "s3" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("model source must be an s3://bucket/prefix URI")
    prefix = urllib.parse.unquote(parsed.path.lstrip("/")).rstrip("/") + "/"
    if prefix == "/" or any(
        part in ("", ".", "..") for part in PurePosixPath(prefix).parts
    ):
        raise ValueError("model source must include a safe non-empty prefix")
    return parsed.netloc, prefix


def list_objects(bucket: str, prefix: str) -> list[dict[str, object]]:
    objects: list[dict[str, object]] = []
    continuation: str | None = None
    while True:
        query = {"list-type": "2", "prefix": prefix, "encoding-type": "url"}
        if continuation:
            query["continuation-token"] = continuation
        url = f"https://{bucket}.s3.amazonaws.com/?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(
            url, headers={"User-Agent": "bossdb-nuclei-build/1"}
        )
        with open_url(request, timeout=120) as response:
            root = ET.parse(response).getroot()
        namespace = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        for content in root.findall("s3:Contents", namespace):
            key_text = content.findtext("s3:Key", namespaces=namespace)
            if not key_text:
                continue
            objects.append(
                {
                    # S3's encoding-type=url represents spaces as '+'.
                    "key": urllib.parse.unquote_plus(key_text),
                    "size": int(content.findtext("s3:Size", "0", namespace)),
                    "etag": content.findtext("s3:ETag", "", namespace).strip('"'),
                }
            )
        truncated = (
            root.findtext("s3:IsTruncated", "false", namespace).lower() == "true"
        )
        if not truncated:
            return objects
        token = root.findtext("s3:NextContinuationToken", namespaces=namespace)
        if not token:
            raise RuntimeError("S3 listing was truncated without a continuation token")
        continuation = token


def _checkpoint_score(entry: dict[str, object]) -> tuple[int, float, str]:
    name = PurePosixPath(str(entry["key"])).name.lower()
    if name.startswith("best"):
        return (0, 0.0, name)
    if "last" in name:
        return (3, math.inf, name)
    match = re.search(r"(?:val[_-]?loss[=:_-]?)?(\d+\.\d+)(?=\.[^.]+$)", name)
    if match:
        return (1, float(match.group(1)), name)
    return (2, math.inf, name)


def select_model_objects(objects: list[dict[str, object]]) -> list[dict[str, object]]:
    """Select the resolved config and single best checkpoint from an S3 listing."""
    files = [entry for entry in objects if not str(entry["key"]).endswith("/")]
    configs = [
        entry
        for entry in files
        if PurePosixPath(str(entry["key"])).name == "config.yaml"
    ]
    if len(configs) != 1:
        raise FileNotFoundError(
            f"expected one config.yaml in the model prefix, found {len(configs)}"
        )
    checkpoints = [
        entry
        for entry in files
        if PurePosixPath(str(entry["key"])).suffix.lower() in _CHECKPOINT_SUFFIXES
    ]
    if not checkpoints:
        raise FileNotFoundError("the model prefix contains no checkpoint")
    checkpoints.sort(key=_checkpoint_score)
    best_score = _checkpoint_score(checkpoints[0])[:2]
    tied = [
        entry for entry in checkpoints if _checkpoint_score(entry)[:2] == best_score
    ]
    if len(tied) > 1 and best_score[0] == 2:
        names = ", ".join(str(entry["key"]) for entry in tied)
        raise ValueError(f"model prefix has multiple unranked checkpoints: {names}")
    return [configs[0], checkpoints[0]]


def download_prefix(s3_uri: str, destination: Path) -> dict[str, object]:
    bucket, prefix = parse_s3_uri(s3_uri)
    objects = list_objects(bucket, prefix)
    files = select_model_objects(objects)
    destination.mkdir(parents=True, exist_ok=True)
    for entry in files:
        key = str(entry["key"])
        if not key.startswith(prefix):
            raise ValueError(
                f"S3 returned an object outside the requested prefix: {key}"
            )
        relative = PurePosixPath(key[len(prefix) :])
        if not relative.parts or any(
            part in ("", ".", "..") for part in relative.parts
        ):
            raise ValueError(f"unsafe model object key: {key}")
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        object_url = (
            f"https://{bucket}.s3.amazonaws.com/{urllib.parse.quote(key, safe='/')}"
        )
        request = urllib.request.Request(
            object_url, headers={"User-Agent": "bossdb-nuclei-build/1"}
        )
        partial = target.with_name(target.name + ".part")
        with open_url(request, timeout=300) as response, partial.open("wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
        if partial.stat().st_size != int(entry["size"]):
            raise OSError(f"incomplete model object: {key}")
        partial.replace(target)

    manifest = {"source": s3_uri, "objects": files}
    (destination / "source.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("s3_uri")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    manifest = download_prefix(args.s3_uri, args.destination)
    print(f"Downloaded {len(manifest['objects'])} model objects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
