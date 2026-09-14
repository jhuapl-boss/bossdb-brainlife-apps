#!/usr/bin/env python3
"""Export a BossDB precomputed cutout as a TIFF volume or TIFF slices."""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import quote, unquote, urlsplit


DEFAULT_BOSSDB_API = "https://api.bossdb.io/v1"
_S3_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


@dataclass(frozen=True)
class ExportConfig:
    channel: str
    x_start: int
    y_start: int
    z_start: int
    x_stop: int
    y_stop: int
    z_stop: int
    resolution: int
    export_as_volume: bool

    @property
    def starts(self) -> tuple[int, int, int]:
        return self.x_start, self.y_start, self.z_start

    @property
    def stops(self) -> tuple[int, int, int]:
        return self.x_stop, self.y_stop, self.z_stop

    @property
    def shape_xyz(self) -> tuple[int, int, int]:
        return tuple(stop - start for start, stop in zip(self.starts, self.stops))


@dataclass(frozen=True)
class BossDBChannel:
    uri: str
    collection: str
    experiment: str
    channel: str
    cloudpath: str


def _required_integer(config: Mapping[str, Any], name: str) -> int:
    value = config.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be an integer")
    integer = int(value)
    if value != integer:
        raise ValueError(f"{name} must be an integer")
    if integer < 0:
        raise ValueError(f"{name} must be non-negative")
    return integer


def validate_config(config: Mapping[str, Any]) -> ExportConfig:
    """Validate the Brainlife configuration and return normalized values."""
    channel = config.get("channel")
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError("channel must be a bossdb:// URI")

    values = {
        name: _required_integer(config, name)
        for name in (
            "x_start", "y_start", "z_start",
            "x_stop", "y_stop", "z_stop", "resolution",
        )
    }
    export_as_volume = config.get("export_as_volume", False)
    if type(export_as_volume) is not bool:
        raise ValueError("export_as_volume must be a boolean")

    for axis in "xyz":
        if values[f"{axis}_stop"] <= values[f"{axis}_start"]:
            raise ValueError(f"{axis}_stop must be greater than {axis}_start")

    return ExportConfig(
        channel=channel.strip(),
        export_as_volume=export_as_volume,
        **values,
    )


def load_config(path: str | Path = "config.json") -> ExportConfig:
    with Path(path).open(encoding="utf-8") as stream:
        raw = json.load(stream)
    if not isinstance(raw, dict):
        raise ValueError("config.json must contain a JSON object")
    return validate_config(raw)


def parse_bossdb_uri(uri: str) -> tuple[str, str, str]:
    """Parse bossdb://collection/experiment/channel into its identifiers."""
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
        raise ValueError("channel must have the form bossdb://collection/experiment/channel")
    if any("/" in value or "\\" in value or value in (".", "..")
           for value in (collection, *parts)):
        raise ValueError("BossDB identifiers may not contain path separators")
    return collection, parts[0], parts[1]


def fetch_json(url: str, timeout: int = 30) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "brainlife-bossdb-tiff/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ValueError("BossDB channel was not found or is not public") from error
        raise RuntimeError(f"BossDB metadata request failed with HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise RuntimeError("BossDB metadata service could not be reached") from error


def _precomputed_cloudpath(metadata: Mapping[str, Any]) -> str:
    if metadata.get("storage_type") != "cloudvol":
        raise ValueError("BossDB channel is not stored as a precomputed CloudVolume")

    bucket = metadata.get("bucket")
    cv_path = metadata.get("cv_path")
    if not isinstance(bucket, str) or not _S3_BUCKET.fullmatch(bucket):
        raise ValueError("BossDB metadata did not provide a valid S3 bucket")
    if not isinstance(cv_path, str) or not cv_path.strip("/"):
        raise ValueError("BossDB metadata did not provide a precomputed path")
    cv_path = cv_path.strip("/")
    path_parts = PurePosixPath(cv_path).parts
    if any(part in ("", ".", "..") for part in path_parts) or "://" in cv_path:
        raise ValueError("BossDB metadata provided an invalid precomputed path")
    return f"s3://{bucket}/{cv_path}"


def resolve_channel(
    uri: str,
    *,
    api_root: str = DEFAULT_BOSSDB_API,
    json_fetcher: Callable[[str], Any] | None = None,
) -> BossDBChannel:
    """Resolve a stable BossDB URI to its public precomputed S3 cloud path."""
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


def open_precomputed_volume(cloudpath: str, mip: int):
    """Open public precomputed data over HTTPS using CloudVolume."""
    from cloudvolume import CloudVolume

    return CloudVolume(
        cloudpath,
        mip=mip,
        bounded=True,
        fill_missing=False,
        progress=False,
        use_https=cloudpath.startswith("s3://"),
    )


def _point_tuple(point: Any) -> tuple[int, int, int]:
    values = tuple(int(value) for value in point)
    if len(values) < 3:
        raise ValueError("CloudVolume returned invalid bounds")
    return values[:3]


def validate_volume_bounds(
    volume: Any, config: ExportConfig
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    bounds = volume.bounds
    minimum = _point_tuple(bounds.minpt)
    maximum = _point_tuple(bounds.maxpt)
    for axis, start, stop, bound_start, bound_stop in zip(
        "xyz", config.starts, config.stops, minimum, maximum
    ):
        if start < bound_start or stop > bound_stop:
            raise ValueError(
                f"requested {axis} range [{start}, {stop}) is outside mip "
                f"bounds [{bound_start}, {bound_stop})"
            )
    return minimum, maximum


def _download_zyx(
    volume: Any,
    starts: tuple[int, int, int],
    stops: tuple[int, int, int],
):
    import numpy as np

    x_start, y_start, z_start = starts
    x_stop, y_stop, z_stop = stops
    downloaded = np.asarray(volume[x_start:x_stop, y_start:y_stop, z_start:z_stop])
    if downloaded.ndim == 4:
        if downloaded.shape[-1] != 1:
            raise ValueError("multi-channel BossDB volumes are not supported")
        downloaded = downloaded[..., 0]
    expected = (x_stop - x_start, y_stop - y_start, z_stop - z_start)
    if downloaded.ndim != 3 or downloaded.shape != expected:
        raise ValueError(
            f"CloudVolume returned shape {downloaded.shape}; expected XYZ shape {expected}"
        )
    return downloaded.transpose(2, 1, 0)


def _write_tiff(path: Path, data: Any, axes: str) -> None:
    import tifffile

    # Classic TIFF offsets are 32-bit. Leave room for TIFF metadata and IFDs.
    bigtiff = data.nbytes >= (4 * 1024**3 - 32 * 1024**2)
    tifffile.imwrite(
        path,
        data,
        bigtiff=bigtiff,
        photometric="minisblack",
        metadata={"axes": axes},
    )


def _z_batch_stop(volume: Any, z_index: int, z_stop: int, z_origin: int) -> int:
    """Return the next source chunk boundary, capped at the requested stop."""
    try:
        chunk_depth = int(volume.chunk_size[2])
    except (AttributeError, IndexError, TypeError, ValueError):
        chunk_depth = 1
    if chunk_depth < 1:
        chunk_depth = 1
    next_boundary = z_origin + ((z_index - z_origin) // chunk_depth + 1) * chunk_depth
    return min(z_stop, next_boundary)


def export_cutout(volume: Any, config: ExportConfig, output_dir: Path) -> dict[str, Any]:
    """Download and write the configured cutout, returning output metadata."""
    output_dir.mkdir(parents=True, exist_ok=True)
    minimum, maximum = validate_volume_bounds(volume, config)
    shape_xyz = config.shape_xyz

    if config.export_as_volume:
        data = _download_zyx(volume, config.starts, config.stops)
        filename = "volume.tif"
        _write_tiff(output_dir / filename, data, "ZYX")
        output = {"mode": "volume", "files": [filename], "file_count": 1}
    else:
        width = max(6, len(str(config.z_stop - 1)))
        z_index = config.z_start
        while z_index < config.z_stop:
            batch_stop = _z_batch_stop(volume, z_index, config.z_stop, minimum[2])
            batch = _download_zyx(
                volume,
                (config.x_start, config.y_start, z_index),
                (config.x_stop, config.y_stop, batch_stop),
            )
            for offset, plane in enumerate(batch):
                plane_z = z_index + offset
                _write_tiff(
                    output_dir / f"slice_z{plane_z:0{width}d}.tif", plane, "YX"
                )
            z_index = batch_stop
        output = {
            "mode": "slices",
            "file_pattern": "slice_z*.tif",
            "file_count": config.z_stop - config.z_start,
        }

    report = {
        "output": output,
        "requested_bounds_xyz": [list(config.starts), list(config.stops)],
        "volume_bounds_xyz": [list(minimum), list(maximum)],
        "shape_xyz": list(shape_xyz),
        "shape_zyx": list(reversed(shape_xyz)),
        "dtype": str(volume.dtype),
        "mip": config.resolution,
    }
    try:
        report["voxel_size_nm_xyz"] = [float(value) for value in volume.resolution]
    except (AttributeError, TypeError, ValueError):
        pass
    return report


def run(
    config: ExportConfig,
    *,
    output_dir: str | Path = "outputs",
    json_fetcher: Callable[[str], Any] | None = None,
    volume_factory: Callable[[str, int], Any] | None = None,
) -> dict[str, Any]:
    channel = resolve_channel(config.channel, json_fetcher=json_fetcher)
    volume = (volume_factory or open_precomputed_volume)(channel.cloudpath, config.resolution)
    report = export_cutout(volume, config, Path(output_dir))
    report.update({
        "source": channel.uri,
        "precomputed_cloudpath": channel.cloudpath,
    })

    output_path = Path(output_dir)
    (output_path / "export.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    Path("product.json").write_text(
        json.dumps({"brainlife": [{
            "type": "success",
            "msg": (
                f"Exported {report['shape_xyz']} voxels at mip {config.resolution} "
                f"as {report['output']['mode']}"
            ),
        }]}) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    try:
        run(load_config())
    except Exception as error:
        print(
            f"BossDB TIFF export failed ({type(error).__name__}). "
            "Check the public channel URI, selected-mip bounds, and resolution.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
