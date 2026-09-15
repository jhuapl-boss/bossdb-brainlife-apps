#!/usr/bin/env bash
# Run Cloudome's complete local contactome pipeline with one command:
#   1. describe the segmentation as cuboid tasks in a temporary file queue;
#   2. drain that queue with one local worker into contactome.raw.db; and
#   3. sum per-cuboid edges into contactome.db (one row per pre/post pair).
#
# Requirements: bash, uv, and a sibling ./cloudome checkout. Cloudome's locked
# Python environment is installed by uv on the first run. Credentials (for
# example AWS_PROFILE) stay in the process environment and are not copied.
#
# Usage:
#   ./run_local_contactome.sh SEGMENTATION_URI [OUTPUT_DIRECTORY]
#
# Example (small public Pinky100 trial):
#   AWS_PROFILE=bossdb ENQUEUE_LIMIT=1000 MIP=64,64,40 \
#     ./run_local_contactome.sh \
#     precomputed://s3://bossdb-open-data/iarpa_microns/pinky/seg pinky-trial
#
# Optional environment variables:
#   GRAPH_ID       Run label; default is a UTC timestamp.
#   MIP            MIP index or xyz resolution; default: 72,72,84.
#   BLOCK_SIZE     Worker cuboid size x,y,z; default: 64,64,32. Each dimension
#                  should be a multiple of the source's chunk size.
#   Z_START/Z_END  Relative Z-voxel bounds; unset means the full Z extent.
#   ENQUEUE_LIMIT  Maximum cuboids to run; use a small value for a smoke test.
#
# Progress and errors are written to contactome.log in the output directory as
# well as displayed in the terminal.
#
# The output directory and database files must be new. This prevents accidental
# mixing of graphs or partial reruns. The temporary queue is removed on exit.
set -Eeuo pipefail

usage() { sed -n '2,/^set -Eeuo pipefail/p' "$0" | sed '$d;s/^# \{0,1\}//'; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[[ ${1:-} != -h && ${1:-} != --help ]] || { usage; exit; }
[[ $# -ge 1 && $# -le 2 ]] || { usage >&2; exit 2; }
command -v uv >/dev/null || die "uv is required (https://docs.astral.sh/uv/)"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cloudome="$root/cloudome"
[[ -f "$cloudome/uv.lock" && -f "$cloudome/local_manage.py" ]] || \
  die "expected the Cloudome checkout at $cloudome"

segmentation_uri=$1
graph_id=${GRAPH_ID:-contactome-$(date -u +%Y%m%dT%H%M%SZ)}
out_dir=${2:-"contactome-output/$graph_id"}
mip=${MIP:-72,72,84}
IFS=, read -r block_x block_y block_z extra <<<"${BLOCK_SIZE:-64,64,32}"
[[ $block_x =~ ^[1-9][0-9]*$ && $block_y =~ ^[1-9][0-9]*$ && \
   $block_z =~ ^[1-9][0-9]*$ && -z ${extra:-} ]] || \
  die "BLOCK_SIZE must contain three positive integers (x,y,z)"

mkdir -p "$out_dir"
out_dir="$(cd "$out_dir" && pwd -P)"
raw_db="$out_dir/contactome.raw.db"
final_db="$out_dir/contactome.db"
log_file="$out_dir/contactome.log"
exec > >(tee -a "$log_file") 2>&1
[[ ! -e $raw_db && ! -e $final_db ]] || \
  die "refusing to overwrite existing databases in $out_dir"

printf 'Contactome log: %s\n' "$log_file"

queue_dir="$(mktemp -d "$out_dir/.cloudome-queue.XXXXXX")"
trap 'rm -rf -- "$queue_dir"' EXIT
queue_url="fq://$queue_dir"

generate=(uv run --frozen python local_manage.py
  --sqlite-db-path "$raw_db" --mip "$mip" --queue-url "$queue_url"
  contactome generate --graph-id "$graph_id"
  --segmentation-channel "$segmentation_uri"
  --block-size-x "$block_x" --block-size-y "$block_y" --block-size-z "$block_z")
[[ -z ${Z_START:-} ]] || generate+=(--z-start "$Z_START")
[[ -z ${Z_END:-} ]] || generate+=(--z-end "$Z_END")
[[ -z ${ENQUEUE_LIMIT:-} ]] || generate+=(--enqueue-limit "$ENQUEUE_LIMIT")

cd "$cloudome"
printf 'Enqueueing graph %s...\n' "$graph_id"
"${generate[@]}"
task_count="$(uv run --frozen python - "$queue_url" <<'PY'
import sys
from taskqueue import TaskQueue
q = TaskQueue(sys.argv[1])
print(q.inserted - q.completed)
PY
)"
[[ $task_count =~ ^[0-9]+$ ]] || die "could not determine the queued task count"
printf 'Processing %s queued cuboid(s)...\n' "$task_count"
(( task_count == 0 )) || uv run --frozen python local_manage.py \
  --queue-url "$queue_url" worker --tally --dequeue-limit "$task_count"
printf 'Aggregating edges...\n'
uv run --frozen python scripts/simplify_contactome_sqlite_db.py \
  "$raw_db" --output-db "$final_db"
printf 'Done: %s\nRaw per-cuboid data: %s\nLog: %s\n' \
  "$final_db" "$raw_db" "$log_file"
