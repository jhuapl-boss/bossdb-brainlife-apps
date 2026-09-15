# BossDB Nuclei Detection

Brainlife inference app that reads a public BossDB image cutout, applies a pretrained 3-D U-Net, and writes the thresholded prediction as a local [Neuroglancer precomputed](https://github.com/google/neuroglancer/blob/master/src/neuroglancer/datasource/precomputed/volume.md) segmentation layer.

The registered test app is [BossDB Nuclei Detection](https://connects.brainlife.io/apps/6aa9705a16d2ef0408f5981a).

The model performs **binary semantic segmentation**. Output value `0` means background and `1` means nucleus. It does not assign a distinct ID to each nucleus; connected components or watershed post-processing can be added later if instance labels are needed.

## Model

The sole model selector is:

```text
20260825_191045 - MONAI BasicUNet 3D
```

It follows `nuclei_3dunet_trial1_inference.yaml` from the nuclei-detector project. The app uses [PyTorch Connectomics](https://github.com/PytorchConnectomics/pytorch_connectomics) for both model construction and inference rather than maintaining a separate implementation:

- The Connectomics `monai_basic_unet3d` factory builds the MONAI BasicUNet with feature widths `(32, 64, 128, 256, 512, 512)`, batch normalization, ReLU, and deconvolution upsampling.
- The Connectomics eager inference engine runs `32 × 128 × 128` ZYX windows, 50% overlap, batches of two windows, and Wu bump blending.
- Connectomics patch-first TTA computes a mean ensemble over all eight combinations of Z, Y, and X flips.
- Sigmoid probabilities with a configurable threshold, default `0.5`.
- Per-cutout `0–1` min-max normalization, matching PyTorch Connectomics' effective test-time transform. (The prepared HDF5 was first z-scored, but the configured framework transform subsequently min-maxed it; that composition is equivalent to min-maxing the raw cutout.)

The app's `pytorch_connectomics` submodule pins PyTorch Connectomics to commit `0d6ae57d5bb011f6b82b13c96fac9bb830ef7aac`, matching the revision in the nuclei-detector repository. Docker installs that checkout directly. This is the `pytorch-connectomics` distribution from the repository above, not the unrelated `connectomics` package on PyPI.

The archived Minnie65 training arrays record an XYZ voxel size of `64 × 64 × 40 nm`. (The inference YAML's `32 × 32 × 40 nm` entry is stale.) Inference does not resample the source. Select the BossDB mip with the archived training voxel size when possible; other physical resolutions are accepted but are out of the training distribution. The source and training resolutions are both recorded in `inference.json`.

The container build lists the public artifacts below and downloads only the resolved `config.yaml` and best validation checkpoint:

```text
s3://bossdb-neuvue-datalake/public/models/20260825_191045 trial 1
```

and bakes them into `/opt/bossdb-nuclei/models/20260825_191045_monai_basic_unet3d`. The resolved `config.yaml` stored with the artifacts is passed to the Connectomics model factory. If the prefix contains several Lightning checkpoints, the loader prefers an explicitly named `best` checkpoint, otherwise the non-`last` checkpoint with the lowest validation loss encoded in its filename. Loading is strict: a missing model config or architecture/checkpoint mismatch fails instead of silently using partial weights.

## BossDB input and coordinates

The `channel` input is a stable URI:

```text
bossdb://collection/experiment/channel
```

The app requests the public channel metadata from `https://api.bossdb.io/v1`, verifies that its storage type is `cloudvol`, and opens the returned S3 precomputed location with CloudVolume over HTTPS. No BossDB or AWS credentials are written to the task configuration or output.

Bounds are half-open selected-mip voxel coordinates: start is inclusive and stop is exclusive. They are not physical coordinates or mip-0 coordinates. Brainlife sometimes serializes numeric fields as strings; integer coordinate strings are accepted.

## Configuration

- `channel`: BossDB reference supplied by the `neuro/bossdb` input.
- `x_start`, `y_start`, `z_start`: inclusive global start coordinate.
- `x_stop`, `y_stop`, `z_stop`: exclusive global stop coordinate.
- `resolution`: zero-based source precomputed mip (`0` is the base mip). If the requested bounds do not fit that mip, the app inspects the channel's scales and corrects the selection only when exactly one bounds-compatible mip has the model's `64 × 64 × 40 nm` training resolution. Valid mip selections are never changed.
- `model`: baked pretrained-model dropdown.
- `threshold`: inclusive probability threshold from `0` through `1`.
- `output_path`: relative local precomputed directory, default `outputs`.

On Brainlife, leave `output_path` set to `outputs`, because that is the registered output subdirectory captured by the platform. A different relative path is useful for direct local execution but will not be collected by the current Brainlife output declaration.

The output directory must be empty. Its precomputed `info` contains one `uint32`, compressed-segmentation mip whose global voxel offset equals the requested start and whose resolution equals the selected source mip. `outputs/inference.json` records the requested and actual source mips, whether correction was needed, bounds, preprocessing, exact checkpoint SHA-256, inference settings, device, threshold, and foreground statistics. `product.json` contains Brainlife's task success message.

## Runtime and resource use

Production execution is intended for one NVIDIA GPU. The `main` launcher enables Apptainer/Singularity NVIDIA passthrough. Inference falls back to CPU for development, but eight-pass 3-D U-Net inference will be slow.

Only two model windows are placed on the GPU at once. PyTorch Connectomics keeps the downloaded image and full-volume accumulators in CPU memory and runs TTA inside each window batch. Budget at least 20 bytes per requested voxel, plus padding to one model window, per-view accumulators, the final mask, CloudVolume buffers, and Python overhead. Start with a modest cutout before scheduling large regions.

## Development

Run dependency-light unit tests locally (model tests are skipped when ML packages are absent):

```sh
python3 -m unittest discover -s tests -v
```

Install the complete Python 3.11 runtime to execute inference directly:

```sh
git submodule update --init --recursive
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock.txt
python download_model.py \
  "s3://bossdb-neuvue-datalake/public/models/20260825_191045 trial 1" \
  models/20260825_191045_monai_basic_unet3d
cp config.json.example config.json
NUCLEI_MODEL_ROOT="$PWD/models" python nuclei_inference.py
```

Build and run the baked container with GPU access:

```sh
docker build --platform linux/amd64 -t bossdb-nuclei:local .
docker run --rm --gpus all -v "$PWD:/work" bossdb-nuclei:local
```

The GitHub workflow tests and publishes an immutable GHCR image tagged with the commit SHA. Brainlife's `main` launcher derives that tag from the checked-out commit. Set `BOSSDB_NUCLEI_IMAGE` to a local SIF or another container URI while debugging on a resource.
