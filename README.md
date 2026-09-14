# BossDB TIFF Export

Brainlife app that exports a rectangular cutout from a public BossDB channel as either one 3D TIFF or a directory of 2D TIFF slices.

The registered test app is [BossDB TIFF Export](https://connects.brainlife.io/apps/6aa8266284691735460ab5f9). It consumes the `neuro/bossdb` reference datatype and writes the test `raw` datatype tagged `tiff`.

## How data access works

The input is a stable URI of the form `bossdb://collection/experiment/channel`. The app requests the public channel metadata from:

```text
https://api.bossdb.io/v1/collection/{collection}/experiment/{experiment}/channel/{channel}
```

For channels whose `storage_type` is `cloudvol`, the returned `bucket` and `cv_path` are combined into an S3 cloud path. CloudVolume opens that location as public precomputed data over HTTPS. The deprecated CloudVolume `boss://` backend and BossDB cutout API are not used.

Only public, precomputed BossDB channels are supported. The app does not store credentials in `config.json`, its output, or its logs.

## Configuration

Brainlife generates `config.json`; `config.json.example` is a small public example.

- `channel`: `bossdb://collection/experiment/channel` reference supplied by the input datatype.
- `x_start`, `y_start`, `z_start`: inclusive cutout start coordinates.
- `x_stop`, `y_stop`, `z_stop`: exclusive cutout stop coordinates.
- `resolution`: zero-based precomputed mip level (`0` is full resolution).
- `export_as_volume`: write `outputs/volume.tif` when true, or `outputs/slice_zNNNNNN.tif` files when false.

Coordinates are voxel indices in the selected mip level, not physical units or mip-0 coordinates. Every range must fit inside that mip's precomputed bounds.

CloudVolume returns data in XYZ order. The exporter transposes it to conventional TIFF ZYX order. Slice mode downloads small Z batches aligned to the precomputed chunk depth, then writes one file per plane; this avoids repeatedly downloading the same source chunk while keeping memory bounded. Volume mode downloads the complete requested cutout into memory before writing it. Neither mode compresses the TIFF pixel data.

An `outputs/export.json` sidecar records the source, selected mip, voxel size in nanometers, bounds, shape, dtype, and output mode. The app also writes Brainlife's root-level `product.json` success message.

## Local development

With a local Python 3.12 environment:

```sh
python -m pip install -r requirements.lock.txt
cp config.json.example config.json
python export_tiff.py
python -m unittest discover -s tests -v
```

Container build and test:

```sh
docker build --platform linux/amd64 -t bossdb-tiff:local .
docker run --rm --entrypoint python -e PYTHONPATH=/opt/bossdb-tiff \
  -v "$PWD/tests:/tests:ro" bossdb-tiff:local \
  -m unittest discover -s /tests -v
docker run --rm -v "$PWD:/work" bossdb-tiff:local
```

The GitHub workflow tests and publishes an immutable GHCR image tagged with the commit SHA. Brainlife's `main` launcher selects that image from the checked-out commit. Set `BOSSDB_TIFF_IMAGE` to a local SIF path while debugging on a resource.
