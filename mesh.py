#!/usr/bin/env python3
"""
mesh.py

Proof-of-concept Brainlife app that runs igneous meshing jobs for a
(precomputed) segmentation layer. Reads parameters from config.json,
executes meshing + manifest passes, and (optionally) moves local outputs.

Usage:
    python mesh.py
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from taskqueue import LocalTaskQueue
import igneous.task_creation as tc


# ----------------------------- Configuration ----------------------------- #

@dataclass
class MeshConfig:
    """
    Container for all app configuration parameters.

    Attributes
    ----------
    cloudpath : str
        Precomputed layer path (e.g., s3://..., gs://..., file://...).
    out_dir : str
        Local output directory (Brainlife "out_dir"). Only used when cloudpath is local (file://).
    mip : int
        Which resolution level to mesh at.
    shape : Tuple[int, int, int]
        Size of each meshing task (xyz). Chunk alignment not required.
    simplification : bool
        Enable quadratic edge-collapse mesh simplification.
    max_simplification_error : float
        Max physical deviation (in voxels) during simplification.
    mesh_dir : Optional[str]
        Custom mesh output directory (relative to layer) or absolute path when using file://.
    cdn_cache : bool
        If True: allow CDN caching; if False: disable to make updates visible immediately.
    dust_threshold : Optional[int]
        Skip objects below this voxel count.
    object_ids : Optional[List[int]]
        If provided, only mesh these labels.
    progress : bool
        Show a progress bar (useful locally).
    fill_missing : bool
        Fill missing chunks with zeros instead of raising an error.
    encoding : str
        'precomputed' or 'draco' (advanced).
    spatial_index : bool
        Generate spatial index for bounding-box queries.
    sharded : bool
        Generate shard fragments for later sharded format.
    manifest_magnitude : int
        Magnitude for manifest pass (controls index granularity).
    parallel : Union[bool, int]
        LocalTaskQueue parallelism. True uses all cores. An int sets max workers.
    """
    cloudpath: str
    out_dir: str
    mip: int = 0
    shape: Tuple[int, int, int] = (256, 256, 256)
    simplification: bool = True
    max_simplification_error: float = 40
    mesh_dir: Optional[str] = None
    cdn_cache: bool = False
    dust_threshold: Optional[int] = None
    object_ids: Optional[List[int]] = None
    progress: bool = False
    fill_missing: bool = False
    encoding: str = "precomputed"
    spatial_index: bool = True
    sharded: bool = False
    manifest_magnitude: int = 3
    parallel: Union[bool, int] = True

    # Multi-res options
    nlod: int = 3  # number of extra LODs
    vqb: int = 10  # vertex quantization bits
    min_chunk_size: Tuple[int, int, int] = (256, 256, 256)
    merge_dir: Optional[str] = None  # optional output directory for merged meshes


def _parse_shape(value: Union[str, Sequence[int]]) -> Tuple[int, int, int]:
    """
    Parse a shape value that may be a space-separated string or a sequence.
    """
    if isinstance(value, str):
        parts = [int(v) for v in value.replace(",", " ").split()]
    else:
        parts = [int(v) for v in value]
    if len(parts) != 3 or any(p <= 0 for p in parts):
        raise ValueError(f"shape must have three positive ints, got {value!r}")
    return (parts[0], parts[1], parts[2])


def _parse_object_ids(value: Optional[Union[str, Sequence[int]]]) -> Optional[List[int]]:
    """
    Parse object_ids which may be a comma/space-separated string or a list.
    """
    if value is None:
        return None
    if isinstance(value, str):
        parts = [p for p in value.replace(",", " ").split() if p]
        return [int(p) for p in parts]
    return [int(v) for v in value]


def load_config(path: str = "config.json") -> MeshConfig:
    """
    Load and validate configuration from a JSON file.

    Notes
    -----
    - `shape` accepts either "X Y Z" (string) or [X, Y, Z] (array).
    - `object_ids` accepts string "1,2,3" / "1 2 3" or a JSON array.
    """
    with open(path, "r") as f:
        raw = json.load(f)

    cfg = MeshConfig(
        cloudpath=raw["cloudpath"],
        out_dir=raw.get("out_dir", "out"),
        mip=int(raw.get("mip", 0)),
        shape=_parse_shape(raw.get("shape", (448, 448, 448))),
        simplification=bool(raw.get("simplification", True)),
        max_simplification_error=float(raw.get("max_simplification_error", 40)),
        mesh_dir=raw.get("mesh_dir"),
        cdn_cache=bool(raw.get("cdn_cache", False)),
        dust_threshold=(int(raw["dust_threshold"]) if raw.get("dust_threshold") is not None else None),
        object_ids=_parse_object_ids(raw.get("object_ids")),
        progress=bool(raw.get("progress", False)),
        fill_missing=bool(raw.get("fill_missing", False)),
        encoding=str(raw.get("encoding", "precomputed")),
        spatial_index=bool(raw.get("spatial_index", True)),
        sharded=bool(raw.get("sharded", False)),
        manifest_magnitude=int(raw.get("manifest_magnitude", 3)),
        parallel=raw.get("parallel", True),
        nlod=int(raw.get("nlod", 0)),
        vqb=int(raw.get("vqb", 16)),
        min_chunk_size=_parse_shape(raw.get("min_chunk_size", (256, 256, 256))),
        merge_dir=raw.get("merge_dir"),
    )
    return cfg


# ------------------------------- Meshing -------------------------------- #

def create_task_queue(parallel: Union[bool, int]) -> LocalTaskQueue:
    """
    Create a LocalTaskQueue with desired parallelism.

    Parameters
    ----------
    parallel : bool | int
        True to use all cores; False to run single-threaded; an int for max workers.
    """
    # LocalTaskQueue takes either bool for "all cores", or an int for pool size.
    return LocalTaskQueue(parallel=parallel)


def queue_meshing_tasks(tq: LocalTaskQueue, cfg: MeshConfig) -> None:
    """
    Build and enqueue meshing tasks (first pass).

    This uses igneous.task_creation.create_meshing_tasks with a broad set of options.
    """
    tasks = tc.create_meshing_tasks(
        layer_path=cfg.cloudpath,
        mip=cfg.mip,
        shape=cfg.shape,
        simplification=cfg.simplification,
        max_simplification_error=cfg.max_simplification_error,
        mesh_dir=cfg.mesh_dir,
        cdn_cache=cfg.cdn_cache,
        dust_threshold=cfg.dust_threshold,
        object_ids=cfg.object_ids,
        progress=cfg.progress,
        fill_missing=cfg.fill_missing,
        encoding=cfg.encoding,
        spatial_index=cfg.spatial_index,
        sharded=cfg.sharded,
    )
    logging.info("Inserting %d meshing tasks", len(tasks))
    tq.insert(tasks)

def queue_merge_tasks(tq: LocalTaskQueue, cfg: MeshConfig) -> None:
    """
    Build and enqueue merge tasks.

    If cfg.nlod > 0, creates multiresolution meshes with extra LODs.
    Otherwise, falls back to single-resolution manifest.
    """
    if cfg.nlod > 0:
        tasks = tc.create_unsharded_multires_mesh_tasks(
            cfg.cloudpath,
            num_lod=cfg.nlod,
            magnitude=cfg.manifest_magnitude,
            mesh_dir=cfg.merge_dir,
            vertex_quantization_bits=cfg.vqb,
            min_chunk_size=cfg.min_chunk_size,
        )
        logging.info(
            "Inserting %d multires merge tasks (nlod=%d, vqb=%d)",
            len(tasks), cfg.nlod, cfg.vqb,
        )
    else:
        tasks = tc.create_mesh_manifest_tasks(
            cfg.cloudpath,
            magnitude=cfg.manifest_magnitude,
            mesh_dir=cfg.merge_dir,
        )
        logging.info("Inserting %d manifest tasks", len(tasks))

    tq.insert(tasks)


def move_local_outputs(cfg: MeshConfig) -> None:
    """
    If cloudpath is local (file://), move resulting files under out_dir.

    Notes
    -----
    - For remote cloudpaths (s3://, gs://, etc.), this is a no-op.
    - For local layers, we copy/move the *layer directory* (minus "file://")
      into out_dir, which Brainlife can then export.
    """
    if not cfg.cloudpath.startswith("file://"):
        logging.info("cloudpath is remote (%s); skipping local file move.", cfg.cloudpath)
        return

    src = cfg.cloudpath.replace("file://", "")
    if not os.path.exists(src):
        raise FileNotFoundError(f"Local cloudpath directory not found: {src}")

    os.makedirs(cfg.out_dir, exist_ok=True)
    dst = os.path.join(cfg.out_dir, os.path.basename(os.path.abspath(src)))
    if os.path.exists(dst):
        logging.warning("Destination %s exists; removing before move.", dst)
        shutil.rmtree(dst)

    logging.info("Moving local outputs %s -> %s", src, dst)
    shutil.move(src, dst)


def run(cfg: MeshConfig) -> None:
    """
    Execute the meshing and manifest passes, then stage outputs if local.

    Steps
    -----
    1) Create task queue with requested parallelism
    2) Enqueue + execute meshing tasks
    3) Enqueue + execute manifest tasks
    4) If using file://, move the layer directory into out_dir
    """
    tq = create_task_queue(cfg.parallel)

    # First pass: Meshing
    queue_meshing_tasks(tq, cfg)
    tq.execute()
    logging.info("Meshing pass complete.")
   
    # NEW: Merge step (single or multires)
    queue_merge_tasks(tq, cfg)
    tq.execute()
    logging.info("Merge pass complete.")
    
    # Stage to Brainlife out_dir if local
    # Disabled for now until I figure out best practice
    move_local_outputs(cfg)


# --------------------------------- Main ---------------------------------- #

def _setup_logging() -> None:
    """
    Configure basic logging suitable for Brainlife logs.
    """
    level = os.environ.get("LOGLEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    Entrypoint. Loads config, runs meshing pipeline, and reports status codes.

    Returns
    -------
    int
        0 if successful; non-zero if an error occurred.
    """
    _setup_logging()
    logging.info("Hello, Brainlife!")

    try:
        cfg = load_config("config.json")
        logging.info("Loaded config for cloudpath=%s mip=%d shape=%s", cfg.cloudpath, cfg.mip, cfg.shape)
        run(cfg)
        logging.info("Done!")
        return 0
    except Exception as e:  # keep broad for POC, tighten in production
        logging.exception("Meshing job failed: %s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
