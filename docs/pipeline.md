> 공개 이력 재구성 진행 중: 이 문서는 완성될 실행 흐름을 설명하며, 현재 단계에서 아직 추가되지 않은 코드는 뒤의 PR에서 공개합니다.

# Reconstruction pipeline

These scripts are a current public adaptation of the orchestration used for the
project. The original Mac workflow produced the demonstrated model. The public
adaptation removes fixed private paths, accepts user configuration, and adds
synthetic tests; it has not been rerun through a complete reconstruction. It is
not a claim that this exact public revision was the historical production code.

FFmpeg extracts frames, COLMAP estimates cameras and sparse geometry, and Brush
trains the Gaussian scene. The Python code coordinates those programs, records
checkpoints, protects output directories, and checks registration. It does not
implement their reconstruction or rendering algorithms.

## Create a base reconstruction

Run commands from the repository root. Python 3.10 or newer is required. The
orchestrator itself uses the Python standard library. Install the external tools
described in [setup.md](setup.md) separately.

Copy `configs/house.example.json` and replace the example video paths and time
ranges with your own. Input paths are resolved relative to the JSON file, not the
shell's working directory. A manifest is an object containing `segments`, or a
nonempty list of segment objects:

```json
{
  "segments": [
    {"path": "../inputs/orbit.mp4", "start": 0, "end": 30, "label": "orbit"},
    {"path": "../inputs/front.mp4", "start": 5, "end": 25, "label": "front"}
  ]
}
```

Times are seconds; `end` is exclusive. Omitted `start` means zero, and omitted
`end` means the probed container duration. Actual extracted frame counts are
recorded separately because container duration and decodable video duration can
differ. Labels become numbered, sanitized filename prefixes.

```sh
python -m pipeline.reconstruct \
  --manifest configs/house.example.json \
  --run-dir runs/house \
  --stop-after sfm
```

The first invocation requires an empty output directory. `--run-dir` selects an
exact directory; without it, a timestamped directory is created under
`--output-root` (default `runs`). For an external disk, add
`--required-mount /path/to/mounted-volume` and put the output inside that mounted
volume. The guard is saved in the run configuration and checked again on resume.

The default preparation is:

1. Probe input video and reject missing video streams, invalid time ranges, and
   PQ/HLG sources that need a separately validated SDR conversion.
2. Extract JPEG frames at 1 fps, fitting inside 1600 × 1600 pixels while retaining
   the aspect ratio. Require at least 16 total frames.
3. Extract up to 6,144 SIFT features per image on CPU using six threads, with one
   shared `SIMPLE_RADIAL` camera for the base clips.
4. Match sequential neighbors, then match global anchor pairs sampled every five
   frames plus each clip's endpoints. `--matcher exhaustive` uses all-pairs
   matching after the sequential pass instead.
5. Run COLMAP mapping, select the component with the most registered images,
   record total and per-clip registration, and create an undistorted dataset.

Options include `--fps`, `--resolution`, `--features`, `--threads`, `--overlap`,
`--global-stride`, `--matcher`, and `--min-registration`. Their values are fixed
for a run; use a new run directory to change preparation settings. The shared
camera assumption requires the same input dimensions and should also be used
only for clips with compatible optics and camera settings. The dimension check
cannot establish that the lens, zoom, or stabilization crop stayed constant.

Inspect `metrics.json`, the sparse camera/point geometry, and the undistorted
images before training. A high registration ratio alone does not prove a
correct or accurate reconstruction.

```sh
python -m pipeline.reconstruct \
  --run-dir runs/house --mode train \
  --brush /path/to/brush_app --steps 6000
```

Brush can also be found as `brush_app` on `PATH`. Default training settings are
6,000 steps, maximum image dimension 1,280, 1.5 million maximum splats, and
spherical-harmonic degree 3. Brush evaluates every 1,000 steps, uses every tenth
image as an evaluation split, and exports periodically. Training is blocked if
the total or any per-clip registration falls below 0.85. The base CLI has an
explicit `--allow-low-registration` override for an inspected result; the
extension CLI does not bypass its per-clip gate.

## Extend a completed reconstruction

`pipeline.extend_reconstruction` adds one clip to a separate copy of a completed
run. The base module has no dependency on this extension module and can be used
on its own.

Copy and edit `configs/extension.example.json`:

```json
{
  "source_run": "../runs/house",
  "output_run": "../runs/expanded",
  "segments": [
    {"path": "../inputs/neighborhood.mp4", "start": 5, "end": 65, "label": "town"}
  ],
  "min_registration": 0.85,
  "cross_match": {
    "source_stride": 3,
    "new_clip_prefix_frames": 65,
    "new_stride": 2
  }
}
```

All paths in this JSON resolve relative to the JSON file. The source and output
runs must be different, non-nested directories. The source must have completed
SfM, its recorded frame/model counts must agree, and its SQLite database must
be closed without WAL/SHM sidecars. Keep the source run idle throughout copying.

```sh
python -m pipeline.extend_reconstruction \
  --config configs/extension.example.json
```

The extension copies the source JPEG frames, feature/match database, and raw
sparse model into its own directory. These are ordinary copies, not hard links.
Database and sparse-model hashes are checked during copying. It preserves the
original run and does not alter the old Gaussian model.

Only the new clip's frames are extracted and passed to feature extraction, with
a separate camera group. Existing features and matches are reused from the
copied database. Additional matching connects the clips: global sampled anchors
plus every `source_stride` old image against every `new_stride` image among the
first `new_clip_prefix_frames` new images. This starting segment should overlap
the old capture; these defaults do not guarantee a match for unrelated footage.

COLMAP receives the copied raw sparse model as the mapping seed. Camera poses
and geometry may be refined while registering the added images: they are not
frozen. The extension records camera-group and cross-clip match statistics and
requires each clip to meet the configured registration ratio before
undistortion. The inherited base image and feature settings stay unchanged;
extension matching always uses the sequential plus sampled cross-clip strategy.

Review the combined camera path, sparse geometry, coverage, and rendering
quality before starting a fresh combined training run:

```sh
python -m pipeline.extend_reconstruction \
  --run-dir runs/expanded --train \
  --brush /path/to/brush_app --steps 12000
```

Extension training defaults to 12,000 steps and two million maximum splats. The
old PLY is not used as an optimizer checkpoint. Old frames, features, matches,
and initial camera poses are reused; Gaussian training starts anew for the
combined undistorted dataset. Existing scene detail can still change or degrade,
so preserving input files is not evidence of unchanged visual quality.

## Checkpoints and local outputs

Each run records `run_config.json`, `metrics.json`, individual command logs,
frames, a COLMAP database and sparse model, an undistorted `dataset/`, and Brush
training attempts under `output/`. Commands and elapsed stage times are recorded
as the work runs. A process lock prevents two public pipeline invocations from
writing one run concurrently; process identity checks also detect surviving
subprocesses from an interrupted invocation.

Resume preparation with the same command and run directory. A completed stage
is reused only if its command signature and output validation agree. A completed
stage with missing expected outputs or a changed command is refused to prevent
silently reusing stale downstream results. Interrupted extraction, mapping, and
undistortion rebuild their owned output for that stage. A failed initialization
can leave a partial directory; inspect it and choose a new empty output rather
than expecting automatic cleanup.

An interrupted training starts a new attempt. A PLY export is a scene snapshot,
not Brush's full optimizer state. A completed matching training attempt can be
reused when its final export remains available.

Inputs are checked by size and nanosecond modification time before extraction;
this is not a cryptographic input-preservation audit. Stage validation also does
not hash every JPEG or detect arbitrary manual changes to run contents. Treat
run files as pipeline-owned, do not edit them during or between resumed stages,
and keep separate source backups. Free-space checks are guardrails, not a peak
RAM, GPU-memory, or total-storage estimate. No maximum video duration is implied:
sampled frame count, scene coverage, matching settings, and hardware capacity
determine practical limits.

Run outputs are local/private data, not repository fixtures. Raw ffprobe tags
(including location and device tags) are not written to the configuration, but
logs/configurations still contain input/output paths, process information, and
capture images. Review any result before publishing it; do not commit input
videos, frames, databases, training exports, or unredacted runtime logs.

## Validation boundaries

The historical workflow was verified on macOS with Apple Silicon. The public
orchestrator uses POSIX process groups and file locks, so Windows is unsupported.
The resource logger selects macOS `/usr/bin/time -l` or Linux `-v`; this does not
mean the complete Linux GPU/toolchain workflow has been validated. The specific
COLMAP and Brush flags follow the versions recorded in [setup.md](setup.md).

The automated tests use synthetic metadata, tiny temporary files, mocked probes,
and a short Python subprocess. They cover manifest validation, output and mount
guards, stage reuse/refusal, exclusive execution, per-clip training gates, and
copy/extension invariants. They do not run FFmpeg, COLMAP, or GPU training and do
not establish reconstruction quality on another capture.

```sh
python -m unittest discover -s tests -p 'test_pipeline.py' -v
python -m unittest discover -s tests -p 'test_extension.py' -v
python -m pipeline.reconstruct --help
python -m pipeline.extend_reconstruction --help
```
