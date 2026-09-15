# Contactome Calculation

Compute a contactome on a dataset, based on the code at https://github.com/aplbrain/cloudome. This method was used in [Matelsky et al. 2026](https://doi.org/10.64898/2026.05.08.723866).

Input a segmentation layer where objects touch each other. Output is a SQLite db with table name contactome_edges and columns graph_id, location, pre, post, weight. Weight is measured in units of square nanometers.

## Local use

Copy `config.json.example` to `config.json` and set `segmentation_uri` to a CloudVolume-compatible segmentation URI. The other fields configure the graph label, output location, resolution, block size, optional Z bounds, and optional smoke-test task limit. The output directory must be new.

Run `./run_local_contactome.sh` (or pass a config file path as its only argument). Store credentials such as `AWS_PROFILE` in the environment, not in `config.json`.
