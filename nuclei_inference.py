#!/usr/bin/env python3
"""Run pretrained 3-D nuclei inference on a public BossDB cutout."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote, unquote, urlsplit

DEFAULT_BOSSDB_API = "https://api.bossdb.io/v1"
MODEL_NAME = "20260825_191045 - MONAI BasicUNet 3D"
MODEL_DIRECTORY = "20260825_191045_monai_basic_unet3d"
MODEL_SOURCE = "s3://bossdb-neuvue-datalake/public/models/20260825_191045 trial 1"
DEFAULT_MODEL_ROOT = Path("/opt/bossdb-nuclei/models")
MODEL_WINDOW_ZYX = (32, 128, 128)
MODEL_OVERLAP = 0.5
MODEL_BATCH_SIZE = 4
MODEL_ARCHITECTURE = "monai_basic_unet3d"
PYTORCH_CONNECTOMICS_REVISION = "0d6ae57d5bb011f6b82b13c96fac9bb830ef7aac"
PYTORCH_CONNECTOMICS_SOURCE = (
    "https://github.com/PytorchConnectomics/pytorch_connectomics"
)
TRAINING_RESOLUTION_XYZ_NM = (32.0, 32.0, 40.0)
_S3_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_CHECKPOINT_SUFFIXES = (".ckpt", ".pth", ".pt")


@dataclass(frozen=True)
class InferenceConfig:
    channel: str
    x_start: int
    y_start: int
    z_start: int
    x_stop: int
    y_stop: int
    z_stop: int
    resolution: int
    model: str
    threshold: float
    output_path: Path

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
    if isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(r"[0-9]+", value):
            raise ValueError(f"{name} must be an integer")
        integer = int(value)
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be an integer")
    else:
        integer = int(value)
        if value != integer:
            raise ValueError(f"{name} must be an integer")
    if integer < 0:
        raise ValueError(f"{name} must be non-negative")
    return integer


def _required_probability(config: Mapping[str, Any], name: str) -> float:
    value = config.get(name)
    if isinstance(value, str):
        value = value.strip()
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number from 0 through 1")
    try:
        probability = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number from 0 through 1") from error
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError(f"{name} must be a number from 0 through 1")
    return probability


def _relative_output_path(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("output_path must be a non-empty relative path")
    path = Path(value.strip())
    if path.is_absolute() or path == Path(".") or ".." in path.parts:
        raise ValueError("output_path must stay inside the task working directory")
    return path


def validate_config(config: Mapping[str, Any]) -> InferenceConfig:
    """Validate Brainlife's configuration and normalize string number fields."""
    channel = config.get("channel")
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError("channel must be a bossdb:// URI")

    values = {
        name: _required_integer(config, name)
        for name in (
            "x_start",
            "y_start",
            "z_start",
            "x_stop",
            "y_stop",
            "z_stop",
            "resolution",
        )
    }
    for axis in "xyz":
        if values[f"{axis}_stop"] <= values[f"{axis}_start"]:
            raise ValueError(f"{axis}_stop must be greater than {axis}_start")

    model = config.get("model", MODEL_NAME)
    if model != MODEL_NAME:
        raise ValueError(f"unsupported model {model!r}; expected {MODEL_NAME!r}")

    return InferenceConfig(
        channel=channel.strip(),
        model=model,
        threshold=_required_probability(config, "threshold"),
        output_path=_relative_output_path(config.get("output_path", "outputs")),
        **values,
    )


def load_config(path: str | Path = "config.json") -> InferenceConfig:
    with Path(path).open(encoding="utf-8") as stream:
        raw = json.load(stream)
    if not isinstance(raw, dict):
        raise ValueError("config.json must contain a JSON object")
    return validate_config(raw)


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
            "channel must have the form bossdb://collection/experiment/channel"
        )
    if any(
        "/" in value or "\\" in value or value in (".", "..")
        for value in (collection, *parts)
    ):
        raise ValueError("BossDB identifiers may not contain path separators")
    return collection, parts[0], parts[1]


def fetch_json(url: str, timeout: int = 30) -> Any:
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
    if any(part in ("", ".", "..") for part in PurePosixPath(cv_path).parts):
        raise ValueError("BossDB metadata provided an invalid precomputed path")
    if "://" in cv_path:
        raise ValueError("BossDB metadata provided an invalid precomputed path")
    return f"s3://{bucket}/{cv_path}"


def resolve_channel(
    uri: str,
    *,
    api_root: str = DEFAULT_BOSSDB_API,
    json_fetcher: Callable[[str], Any] | None = None,
) -> BossDBChannel:
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
    volume: Any, config: InferenceConfig
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    minimum = _point_tuple(volume.bounds.minpt)
    maximum = _point_tuple(volume.bounds.maxpt)
    for axis, start, stop, bound_start, bound_stop in zip(
        "xyz", config.starts, config.stops, minimum, maximum
    ):
        if start < bound_start or stop > bound_stop:
            raise ValueError(
                f"requested {axis} range [{start}, {stop}) is outside mip "
                f"bounds [{bound_start}, {bound_stop})"
            )
    return minimum, maximum


def download_cutout_zyx(volume: Any, config: InferenceConfig):
    import numpy as np

    validate_volume_bounds(volume, config)
    downloaded = np.asarray(
        volume[
            config.x_start : config.x_stop,
            config.y_start : config.y_stop,
            config.z_start : config.z_stop,
        ]
    )
    if downloaded.ndim == 4:
        if downloaded.shape[-1] != 1:
            raise ValueError("multi-channel BossDB volumes are not supported")
        downloaded = downloaded[..., 0]
    if downloaded.ndim != 3 or downloaded.shape != config.shape_xyz:
        raise ValueError(
            f"CloudVolume returned shape {downloaded.shape}; expected XYZ shape "
            f"{config.shape_xyz}"
        )
    return downloaded.transpose(2, 1, 0)


def normalize_em(volume_zyx: Any):
    """Apply PyTorch Connectomics' effective test-time 0-1 normalization."""
    import numpy as np

    normalized = np.asarray(volume_zyx, dtype=np.float32)
    minimum = float(normalized.min())
    maximum = float(normalized.max())
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        raise ValueError("the input cutout contains non-finite intensities")
    if maximum <= minimum:
        raise ValueError("the input cutout has no intensity variation")
    normalized = (normalized - minimum) / (maximum - minimum)
    return normalized, {
        "method": "min-max",
        "output_range": [0.0, 1.0],
        "minimum": minimum,
        "maximum": maximum,
    }


def _checkpoint_score(path: Path) -> tuple[int, float, str]:
    name = path.name.lower()
    if name.startswith("best"):
        return (0, 0.0, name)
    if "last" in name:
        return (3, math.inf, name)
    match = re.search(r"(?:val[_-]?loss[=:_-]?)?(\d+\.\d+)(?=\.[^.]+$)", name)
    if match:
        return (1, float(match.group(1)), name)
    return (2, math.inf, name)


def find_checkpoint(model_directory: str | Path) -> Path:
    root = Path(model_directory)
    candidates = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in _CHECKPOINT_SUFFIXES
        ),
        key=_checkpoint_score,
    )
    if not candidates:
        raise FileNotFoundError(f"no checkpoint found under {root}")
    best_score = _checkpoint_score(candidates[0])[:2]
    tied = [path for path in candidates if _checkpoint_score(path)[:2] == best_score]
    if len(tied) > 1 and best_score[0] == 2:
        names = ", ".join(str(path.relative_to(root)) for path in tied)
        raise ValueError(f"model directory has multiple unranked checkpoints: {names}")
    return candidates[0]


def find_model_config(model_directory: str | Path) -> Path:
    """Return the resolved PyTorch Connectomics config stored with the checkpoint."""
    config_path = Path(model_directory) / "config.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"model config not found: {config_path}")
    return config_path


def checkpoint_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def select_model_state_dict(
    checkpoint: Mapping[str, Any], expected_keys: Sequence[str]
) -> dict[str, Any]:
    """Extract model weights from common PyTorch Lightning checkpoint layouts."""
    raw: Any = checkpoint
    for field in ("state_dict", "model_state_dict"):
        if isinstance(checkpoint.get(field), Mapping):
            raw = checkpoint[field]
            break
    if not isinstance(raw, Mapping):
        raise ValueError("checkpoint does not contain a state dictionary")

    expected = set(expected_keys)
    prefixes = (
        "model.model.",
        "model._orig_mod.model.",
        "model._orig_mod.",
        "model.",
        "module.model.",
        "module.",
        "_orig_mod.",
        "",
    )
    options: list[tuple[int, dict[str, Any]]] = []
    for prefix in prefixes:
        transformed = {
            key[len(prefix) :]: value
            for key, value in raw.items()
            if isinstance(key, str)
            and key.startswith(prefix)
            and key[len(prefix) :] in expected
        }
        options.append((len(transformed), transformed))
    count, selected = max(options, key=lambda item: item[0])
    missing = sorted(expected.difference(selected))
    if count != len(expected):
        preview = ", ".join(missing[:5])
        raise ValueError(
            f"checkpoint is incompatible with {MODEL_NAME}: found "
            f"{count}/{len(expected)} "
            f"expected tensors; missing {preview}"
        )
    return selected


def load_connectomics_model_config(config_path: str | Path):
    """Load and validate the resolved training config baked beside the weights."""
    from omegaconf import OmegaConf

    config = OmegaConf.load(config_path)
    model_config = config.get("model")
    if model_config is None:
        raise ValueError("model config does not contain a model section")
    architecture = model_config.get("arch", {}).get("type")
    if architecture != MODEL_ARCHITECTURE:
        raise ValueError(
            f"model config architecture is {architecture!r}; "
            f"expected {MODEL_ARCHITECTURE!r}"
        )
    if int(model_config.get("in_channels", 0)) != 1:
        raise ValueError("model config must define one input channel")
    if int(model_config.get("out_channels", 0)) != 1:
        raise ValueError("model config must define one output channel")
    return config


def load_model(
    checkpoint_path: str | Path,
    device: str,
    model_config_path: str | Path,
):
    import torch
    from connectomics.models import build_model

    connectomics_config = load_connectomics_model_config(model_config_path)
    model = build_model(connectomics_config)
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    if not isinstance(checkpoint, Mapping):
        raise ValueError("checkpoint root must be a mapping")
    state_dict = select_model_state_dict(checkpoint, tuple(model.state_dict()))
    model.load_state_dict(state_dict, strict=True)
    model.eval()
    return model.to(device)


def choose_device() -> tuple[str, str]:
    import torch

    requested = os.environ.get("NUCLEI_DEVICE", "auto").strip().lower()
    if requested == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    elif requested in ("cpu", "cuda"):
        device = requested
    else:
        raise ValueError("NUCLEI_DEVICE must be auto, cpu, or cuda")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")
    if device == "cuda":
        return device, torch.cuda.get_device_name(torch.cuda.current_device())
    return device, "CPU"


def build_connectomics_inference_config(
    device: str,
    *,
    window_zyx: tuple[int, int, int] = MODEL_WINDOW_ZYX,
    overlap: float = MODEL_OVERLAP,
    batch_size: int = MODEL_BATCH_SIZE,
):
    """Build the small runtime config consumed by Connectomics inference APIs."""
    return SimpleNamespace(
        model=SimpleNamespace(out_channels=1, primary_head=None, heads={}),
        data=SimpleNamespace(
            train=SimpleNamespace(do_2d=False),
            val=SimpleNamespace(do_2d=False),
            dataloader=SimpleNamespace(batch_size=max(1, int(batch_size))),
        ),
        inference=SimpleNamespace(
            model=SimpleNamespace(
                head=None,
                select_channel=None,
                output_dtype="float32",
                channel_activations=[{"channels": ":", "activation": "sigmoid"}],
            ),
            sliding_window=SimpleNamespace(
                enabled=True,
                window_size=[int(value) for value in window_zyx],
                sw_batch_size=max(1, int(batch_size)),
                overlap=float(overlap),
                blending="bump",
                padding_mode="reflect",
                cval=0.0,
                keep_input_on_cpu=True,
                distributed_sharding=False,
                sw_device=device,
                output_device="cpu",
                border_mask=[],
            ),
            test_time_augmentation=SimpleNamespace(
                enabled=True,
                distributed_sharding=False,
                flip_axes="all",
                rotation90_axes=None,
                rotate90_k=None,
                patch_first_local=True,
                apply_mask=True,
                ensemble_mode="mean",
                empty_cache_interval=4,
            ),
        ),
    )


def predict_probabilities(
    image_zyx: Any,
    model: Any,
    device: str,
    *,
    window_zyx: tuple[int, int, int] = MODEL_WINDOW_ZYX,
    overlap: float = MODEL_OVERLAP,
    batch_size: int = MODEL_BATCH_SIZE,
):
    """Run PyTorch Connectomics sliding-window prediction and flip TTA."""
    import numpy as np
    import torch
    from connectomics.inference.manager import InferenceManager

    image = np.asarray(image_zyx, dtype=np.float32)
    if image.ndim != 3:
        raise ValueError(f"inference input must be ZYX, got shape {image.shape}")
    print(
        "Running PyTorch Connectomics inference with 8-way flip TTA, "
        f"{window_zyx} ZYX windows, {overlap:.0%} overlap, and "
        f"batches of {max(1, batch_size)}",
        flush=True,
    )
    runtime_config = build_connectomics_inference_config(
        device,
        window_zyx=window_zyx,
        overlap=overlap,
        batch_size=batch_size,
    )
    manager = InferenceManager(runtime_config, model=model, forward_fn=model)
    tensor = torch.from_numpy(np.ascontiguousarray(image))[None, None]
    with torch.inference_mode():
        prediction = manager.predict_with_tta(tensor)
    expected_shape = (1, 1, *image.shape)
    if (
        not isinstance(prediction, torch.Tensor)
        or tuple(prediction.shape) != expected_shape
    ):
        raise ValueError(
            f"Connectomics returned shape {getattr(prediction, 'shape', None)}; "
            f"expected {expected_shape}"
        )
    return prediction.detach().float().cpu().numpy()[0, 0]


def write_precomputed_segmentation(
    output_path: Path,
    segmentation_zyx: Any,
    *,
    starts_xyz: Sequence[int],
    resolution_xyz: Sequence[float],
):
    import numpy as np
    from cloudvolume import CloudVolume

    segmentation = np.asarray(segmentation_zyx, dtype=np.uint32)
    if segmentation.ndim != 3:
        raise ValueError("segmentation output must be a 3-D ZYX array")
    if output_path.exists():
        if not output_path.is_dir():
            raise FileExistsError(f"output_path is not a directory: {output_path}")
        if any(output_path.iterdir()):
            raise FileExistsError(f"output_path is not empty: {output_path}")
    output_path.mkdir(parents=True, exist_ok=True)

    shape_xyz = tuple(reversed(segmentation.shape))
    info = CloudVolume.create_new_info(
        num_channels=1,
        layer_type="segmentation",
        data_type="uint32",
        encoding="compressed_segmentation",
        resolution=[float(value) for value in resolution_xyz],
        voxel_offset=[int(value) for value in starts_xyz],
        volume_size=list(shape_xyz),
        chunk_size=(64, 64, 64),
        compressed_segmentation_block_size=(8, 8, 8),
        max_mip=0,
    )
    volume = CloudVolume(
        output_path.resolve().as_uri(),
        info=info,
        mip=0,
        bounded=True,
        fill_missing=False,
        progress=False,
        parallel=False,
    )
    volume.commit_info()
    x_start, y_start, z_start = (int(value) for value in starts_xyz)
    x_stop, y_stop, z_stop = (
        start + size for start, size in zip((x_start, y_start, z_start), shape_xyz)
    )
    xyz = segmentation.transpose(2, 1, 0)[..., np.newaxis]
    volume[x_start:x_stop, y_start:y_stop, z_start:z_stop] = xyz
    return info


def _volume_resolution(volume: Any) -> tuple[float, float, float]:
    values = tuple(float(value) for value in volume.resolution)
    if len(values) < 3 or any(
        not math.isfinite(value) or value <= 0 for value in values[:3]
    ):
        raise ValueError("CloudVolume returned an invalid voxel resolution")
    return values[:3]


def run(
    config: InferenceConfig,
    *,
    json_fetcher: Callable[[str], Any] | None = None,
    volume_factory: Callable[[str, int], Any] | None = None,
    model_loader: Callable[[Path, str], Any] | None = None,
    predictor: Callable[..., Any] | None = None,
    output_writer: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    import numpy as np

    started = time.monotonic()
    print(f"Resolving {config.channel}", flush=True)
    channel = resolve_channel(config.channel, json_fetcher=json_fetcher)
    volume = (volume_factory or open_precomputed_volume)(
        channel.cloudpath, config.resolution
    )
    volume_minimum, volume_maximum = validate_volume_bounds(volume, config)
    resolution_xyz = _volume_resolution(volume)
    print(
        f"Downloading XYZ cutout {config.starts} to {config.stops} at mip "
        f"{config.resolution} ({resolution_xyz} nm)",
        flush=True,
    )
    if not np.allclose(resolution_xyz, TRAINING_RESOLUTION_XYZ_NM):
        print(
            "WARNING: selected source resolution differs from the model's "
            f"{TRAINING_RESOLUTION_XYZ_NM} nm training resolution",
            flush=True,
        )
    image_zyx = download_cutout_zyx(volume, config)
    normalized, normalization = normalize_em(image_zyx)

    device, device_name = choose_device()
    model_root = Path(os.environ.get("NUCLEI_MODEL_ROOT", DEFAULT_MODEL_ROOT))
    model_directory = model_root / MODEL_DIRECTORY
    checkpoint = find_checkpoint(model_directory)
    model_config = find_model_config(model_directory)
    print(
        f"Loading {config.model} from {checkpoint.name} on {device_name}",
        flush=True,
    )
    model = (
        model_loader(checkpoint, device)
        if model_loader is not None
        else load_model(checkpoint, device, model_config)
    )
    probabilities = (predictor or predict_probabilities)(normalized, model, device)
    probabilities = np.asarray(probabilities, dtype=np.float32)
    if probabilities.shape != normalized.shape:
        raise ValueError(
            f"predictor returned ZYX shape {probabilities.shape}; "
            f"expected {normalized.shape}"
        )
    if not np.isfinite(probabilities).all():
        raise ValueError("model produced non-finite probabilities")
    segmentation = (probabilities >= config.threshold).astype(np.uint32)
    foreground_voxels = int(segmentation.sum(dtype=np.uint64))

    print(f"Writing precomputed segmentation to {config.output_path}", flush=True)
    (output_writer or write_precomputed_segmentation)(
        config.output_path,
        segmentation,
        starts_xyz=config.starts,
        resolution_xyz=resolution_xyz,
    )
    model_hash = checkpoint_sha256(checkpoint)
    elapsed = time.monotonic() - started
    report = {
        "source": channel.uri,
        "source_precomputed_cloudpath": channel.cloudpath,
        "source_mip": config.resolution,
        "source_volume_bounds_xyz": [list(volume_minimum), list(volume_maximum)],
        "requested_bounds_xyz": [list(config.starts), list(config.stops)],
        "shape_xyz": list(config.shape_xyz),
        "shape_zyx": list(reversed(config.shape_xyz)),
        "voxel_size_nm_xyz": list(resolution_xyz),
        "training_voxel_size_nm_xyz": list(TRAINING_RESOLUTION_XYZ_NM),
        "training_voxel_size_match": bool(
            np.allclose(resolution_xyz, TRAINING_RESOLUTION_XYZ_NM)
        ),
        "normalization": normalization,
        "model": {
            "name": config.model,
            "architecture": MODEL_ARCHITECTURE,
            "source": MODEL_SOURCE,
            "checkpoint": str(checkpoint.relative_to(model_root)),
            "checkpoint_sha256": model_hash,
            "config": str(model_config.relative_to(model_root)),
            "config_sha256": checkpoint_sha256(model_config),
            "framework": {
                "name": "PyTorch Connectomics",
                "source": PYTORCH_CONNECTOMICS_SOURCE,
                "revision": PYTORCH_CONNECTOMICS_REVISION,
            },
            "window_zyx": list(MODEL_WINDOW_ZYX),
            "overlap": MODEL_OVERLAP,
            "blending": "Wu bump",
            "sliding_window_batch_size": MODEL_BATCH_SIZE,
            "test_time_augmentation": "mean of all 8 spatial flip combinations",
            "small_cutout_padding": (
                "reflect per window; trailing constant zero when an axis is "
                "smaller than its window"
            ),
        },
        "threshold": config.threshold,
        "output": {
            "path": str(config.output_path),
            "format": "Neuroglancer precomputed",
            "layer_type": "segmentation",
            "encoding": "compressed_segmentation",
            "dtype": "uint32",
            "labels": {"0": "background", "1": "nucleus"},
            "foreground_voxels": foreground_voxels,
            "foreground_fraction": foreground_voxels / segmentation.size,
        },
        "runtime": {"device": device, "device_name": device_name, "seconds": elapsed},
    }
    (config.output_path / "inference.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    Path("product.json").write_text(
        json.dumps(
            {
                "brainlife": [
                    {
                        "type": "success",
                        "msg": (
                            "Detected nuclei in "
                            f"{list(config.shape_xyz)} voxels at mip "
                            f"{config.resolution}; "
                            f"{foreground_voxels} foreground voxels"
                        ),
                    }
                ]
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    try:
        run(load_config())
    except Exception as error:
        print(
            f"BossDB nuclei inference failed ({type(error).__name__}): {error}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
