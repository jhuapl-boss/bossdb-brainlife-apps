<h1 align="center">bossdb brainlife apps</h1>

<p align="center">
  Computes a contactome (contact-area graph between touching segmented objects) from a BossDB precomputed segmentation with Cloudome, writing a SQLite database.
</p>

<p align="center">
  <a href="https://github.com/brainlife/abcd-spec"><img src="https://img.shields.io/badge/ABCD_Spec-v1.1-green.svg" alt="ABCD Spec v1.1"></a>
  <a href="https://doi.org/10.25663/brainlife.app.971"><img src="https://img.shields.io/badge/Brainlife-brainlife.app.971-blue.svg" alt="Run on brainlife.io"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-see_LICENSE-lightgrey.svg" alt="License"></a>
</p>

## At a glance

<!-- brainlife-app:begin (generated from the App manifest; edit the manifest, not this block) -->

> **Computes a contactome (contact-area graph between touching segmented objects) from a BossDB precomputed segmentation with Cloudome, writing a SQLite database.**

Resolves a BossDB segmentation channel to its precomputed CloudVolume location, splits it into cuboid tasks, runs Cloudome workers locally and sums per-cuboid contacts into contactome.db (table contactome_edges: graph_id, location, pre, post, weight in square nanometres). Use it to build a contact graph between neighbouring objects in an EM segmentation. The input must be a segmentation where objects touch; the output directory and databases must not already exist.

| | |
|---|---|
| **Inputs** | `neuro/bossdb` |
| **Outputs** | `raw` |
| **Container** | `ghcr.io/jhuapl-boss/bossdb-brainlife-apps:cloudome` |
| **Run it** | [doi.org/10.25663/brainlife.app.971](https://doi.org/10.25663/brainlife.app.971) |

#### Inputs

| Input | Datatype | Required | Description |
|---|---|---|---|
| `seg` | `neuro/bossdb` | yes | BossDB segmentation channel reference of the form bossdb://collection/experiment/channel. Public channel stored as precomputed CloudVolume data, containing a segmentation whose objects touch. |

#### Outputs

| Output | Datatype | Description |
|---|---|---|
| `output` | `raw` | SQLite database contactome.db (table contactome_edges with graph_id, location, pre, post, weight in square nanometres) and a contactome.log. |

<details>
<summary><b>Parameters</b> (8)</summary>

| Parameter | Type | Default | Description |
|---|---|---|---|
| `output_directory` | string | `outputs` | Directory in which output will be written |
| `graph_id` | string | `contactome-example` | When storing more than one contactome in the same DB, this parameter differentiates them |
| `mip` | string |  | The resolution of the segmentation at which the contactome should be calculated |
| `block_size` | string | `64,64,32` | Computations are calculated blockwise; this parameter specifies how large blocks should be. Scale this based on the memory capacity of the target machine |
| `z_start` | number |  | Subset the computation so that it starts at a specific z slice |
| `z_end` | number |  | Subset the computation so that it ends at a specific z slice |
| `enqueue_limit` | number |  | For testing, truncate the queue so that only this many blocks are computed |
| `workers` | number | `1` | Number of local Cloudome worker processes that drain the task queue. |

</details>

<!-- brainlife-app:end -->

## Versions on brainlife.io

This repository hosts several Apps, each built from its own branch.

| App | Branch | Run it | Status |
|---|---|---|---|
| BossDB Contactome | [`cloudome`](https://github.com/jhuapl-boss/bossdb-brainlife-apps/tree/cloudome) | [doi:10.25663/brainlife.app.971](https://doi.org/10.25663/brainlife.app.971) | current |
| BossDB Nuclei Segmentation | [`apex-hackathon-2026-segmentation`](https://github.com/jhuapl-boss/bossdb-brainlife-apps/tree/apex-hackathon-2026-segmentation) | [doi:10.25663/brainlife.app.970](https://doi.org/10.25663/brainlife.app.970) | available |
| BossDB TIFF Export | [`apex-hackathon-2026`](https://github.com/jhuapl-boss/bossdb-brainlife-apps/tree/apex-hackathon-2026) | [doi:10.25663/brainlife.app.967](https://doi.org/10.25663/brainlife.app.967) | available |
| Precomputed Multi-resolution Mesh Generation | [`v0.1`](https://github.com/jhuapl-boss/bossdb-brainlife-apps/tree/v0.1) | [doi:10.25663/brainlife.app.870](https://doi.org/10.25663/brainlife.app.870) | available |

## Running the App

### On brainlife.io

Open the App at [doi.org/10.25663/brainlife.app.971](https://doi.org/10.25663/brainlife.app.971), choose `neuro/bossdb` from your project, set the parameters and press **Execute**. The results are saved to your project as `raw`.

### Locally

You need [Singularity/Apptainer](https://apptainer.org/) (the App runs in `ghcr.io/jhuapl-boss/bossdb-brainlife-apps:cloudome`), [`jq`](https://jqlang.github.io/jq/).

1. Clone this repository and check out the App's branch (`git checkout cloudome`).
2. Create `config.json` with the paths to your inputs and the parameters:

   ```json
   {
       "channel": "/path/to/seg/channel",
       "output_directory": "outputs",
       "graph_id": "contactome-example",
       "mip": "",
       "block_size": "64,64,32",
       "z_start": "<number>",
       "z_end": "<number>",
       "enqueue_limit": "<number>",
       "workers": 1
   }
   ```

3. Run `./main`.

## Troubleshooting

- **refusing to overwrite existing databases**: Use a new, empty output directory.

## Citing

If you use this App, please cite it, the software it builds on and brainlife.io:

- BossDB Contactome. brainlife.io App. [doi:10.25663/brainlife.app.971](https://doi.org/10.25663/brainlife.app.971)
- Matelsky et al. 2026. [doi:10.64898/2026.05.08.723866](https://doi.org/10.64898/2026.05.08.723866)
- Hayashi S, Caron BA, Heinsfeld AS, et al. brainlife.io: a decentralized and open-source cloud platform to support neuroscience research. *Nature Methods* 21:809–813 (2024). [doi:10.1038/s41592-024-02237-2](https://doi.org/10.1038/s41592-024-02237-2)
- Avesani P, McPherson B, Hayashi S, et al. The open diffusion data derivatives, brain data upcycling via integrated publishing of derivatives and reproducible open cloud services. *Scientific Data* 6:69 (2019). [doi:10.1038/s41597-019-0073-y](https://doi.org/10.1038/s41597-019-0073-y)

## Authors

Maintained by [@jhuapl-boss](https://github.com/jhuapl-boss).

## License

See [LICENSE](LICENSE).
