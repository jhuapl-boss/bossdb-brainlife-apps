# NWB Reference Inspection

Inspect metadata and a small numeric sample from a reference dataset in place.

Runs with Apptainer or Singularity on AWS Batch and non-AWS resources. Provide config.json. The app writes report/report.json and product.json. The resource must have access to private stores. Source data stays remote.

## Inputs and outputs

Provide `config.json` based on `config.json.example`; Brainlife generates it from the app settings. `input` is an HTTPS URL or S3 URI for a reference dataset, or a local path for direct testing. `kind` selects `nwb` or `ome-zarr`. The default sample is 64 numeric elements, with an upper limit of 4,096. `array_path` optionally selects an HDF5 dataset or OME multiscale array.

NWB reads use HTTP range requests. OME-Zarr reads metadata and selected chunks without copying the complete store. Chunk transfer/decompression can exceed the sample size. The app supports Zarr v2 and v3 and does not perform a full scientific analysis or full dataset validation.

The app writes `report/index.html`, `report/html/report.json`, `report/report.json` and a success message in `product.json`. Brainlife archives the `report` directory as `report/html`. Input URLs and credentials are excluded from these reports.

Local container test:

```sh
docker build --platform linux/amd64 -t reference-inspect:local .
docker run --rm --entrypoint python -e PYTHONPATH=/opt/reference-inspect \
  -v "$PWD/tests:/tests:ro" reference-inspect:local -m unittest discover -s /tests -v
```

For private stores, leave `anonymous` false and provision credentials on the resource. For public S3 stores, set `anonymous` true. To use a locally built SIF during debugging, set `REFERENCE_INSPECT_IMAGE` to its path.

[GitHub's container publishing documentation](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images) describes the built-in token workflow.
