# HYCOM ATLb2.00 Currents

A **virtual** [Icechunk](https://icechunk.io) store of the global RTOFS ocean-velocity archive (HYCOM
`ATLb2.00`, 1/12° tripolar) that TAMU-CC keeps at
<https://hriai01.tamucc.edu/data/MASTR_MODELS/rtofs/history/>. The store holds only Zarr metadata and
byte-range references (about 110 MB). **The science data stays in the original NetCDF-4 files on
TAMU-CC's web server**, so every data read depends on that server being up and on its files not being
moved or rewritten.

| | |
|---|---|
| Model | Global RTOFS (HYCOM `ATLb2.00`, 1/12° tripolar), experiments 93.0 and 93.1 |
| Variables | `u`, `v`: eastward / northward sea water velocity, m s-1, float32. No temperature, salinity or SSH in the source |
| Grid | 33 z-levels 0–5,500 m × 3,298 (`Y`) × 4,500 (`X`); 2-D `Latitude`/`Longitude`, longitude in [-180, 180) |
| Coverage | 267 daily 00 UTC runs, 2025-06-16 … 2026-11-17; `/best` valid days 2025-06-17 … 2026-11-25 (527 days, 245 with no run) |
| Source | 3,682 NetCDF-4/HDF5 files (≈ 3.3 TB), one per run × valid day × variable; 3,680 referenced, 2 unreadable excluded |
| Stores | `rtofs_icechunks_v1/` (Icechunk format v1) and `rtofs_icechunks/` (format v2); both local, git-ignored. See [Two stores](#two-stores-icechunk-format-v1-and-v2) |
| Current snapshot | v1: `C4GZ0VESHJM01E8Q3WJG` (2026-11-19) · v2: `XKG4RQMNKYYD6DM5S47G` (2026-11-18); both tagged `data-through-2026-11-25` |

## Groups

Each source file is **one variable, one valid day, from one forecast run**: `<run>.<valid>.<u|v>.nc`,
e.g. `2026/11/2026-11-17-00.2026-11-25-00.v.nc`. A run normally covers leads 1–8 days. Both groups
reference the **same** source bytes; neither copies anything.

| Group | Dimensions | Use it for |
|---|---|---|
| `/forecast` | `forecast_reference_time` (267) × `lead` (1–8 d) × `Depth` × `Y` × `X` | comparing runs, forecast skill by lead. `valid_time(forecast_reference_time, lead)` gives the clock time; `experiment` and `n_leads` per run. |
| `/best` | `time` (527 daily) × `Depth` × `Y` × `X` | a single continuous series. Each day comes from the **newest run with readable u and v**; `forecast_reference_time(time)`, `lead(time)` and `experiment(time)` record which. |

- **The last seven days of `/best` are forecasts** (leads 2–8 of the newest run). They are replaced as newer
  runs arrive; check `lead` if analysis-quality fields matter.
- **No runs from 2025-12-11 to 2026-08-19** (and none on 2025-06-17): 245 `/best` days have no chunk and
  read as NaN (`lead` = −1 day). The time axis is still continuous and daily.
- **Shorter runs are padded to 8 leads** (owner decision 2026-11-18). 2025-06-16 … 09-17 runs have 5 lead
  days and 2026-08-21 … 08-24 have 4; missing leads read as NaN, `n_leads` says how many are real.
- **Both model versions kept**, tagged per run by `experiment` (93.0 through the 2025-07-28 run, 93.1 from 2025-07-29). Filter
  with `fc.where(fc.experiment == "93.1", drop=True)`.

### Excluded source files

| Excluded | Reason |
|---|---|
| `2025/07/2025-07-29-00.2025-08-01-00.v.nc` | HDF5 metadata checksum error (h5py and netCDF-C both refuse it) |
| `2025/07/2025-07-29-00.2025-08-03-00.v.nc` | same |

In `/forecast` that run's `v` at those leads is NaN; `/best` takes those days from the next run, so it
loses nothing. They are retried automatically if their modification time changes. `source_manifest.csv`
lists every discovered file and what the build did with it.

## Chunking

Nothing is rechunked: a virtual store can only point at the chunks the provider wrote. Each `u`/`v` chunk is
an HDF5 chunk inside one source file.

| | |
|---|---|
| Source chunk (`u`, `v`) | **1 time × 3 depths × 413 × 563**, float32, zlib level 1 (`numcodecs.zlib`) |
| Chunk size | 2.79 MB uncompressed, about **1.2 MB** compressed |
| Chunks per 3-D field (one variable, one day) | 11 (depth) × 8 (Y) × 8 (X) = **704**; the last Y and X chunks are partial (3,298 = 7 × 413 + 407; 4,500 = 7 × 563 + 559) |
| Chunk references in the store | `/forecast`: 267 × 8 × 704 × 2 ≈ 3.0 M; `/best`: 527 × 704 × 2 ≈ 0.74 M |
| Manifest splitting | references split every 30 runs (`/forecast`) and 120 days (`/best`), so a reader of recent data does not load the whole history's references |
| Materialized (copied into the store) | coordinates only: `Latitude`/`Longitude` (59 MB each, 4 chunks, stored once and kept while their hash is unchanged), `Depth`, time coordinates (one chunk each) |

What that means for reads:

- **Maps and regional windows are cheap.** The Gulf of Mexico at 0–20 m for one day is 8 chunks per variable.
- **Point time series cost one chunk per day per variable** (and per 3 depths), because time is the outer,
  unchunked-together axis: one surface point for the whole `/best` record is 282 days × 2 variables =
  564 requests (≈ 680 MB fetched for 2 × 282 values).
- **A full 3-D global field** is 704 chunks (≈ 860 MB) per variable.

This layout is the provider's and cannot be changed without rewriting and republishing the files. If
time-series access becomes the main use, a *materialized* time-chunked copy of a region would be the fix,
not a different virtual store.

## Loading

No credentials are needed for either half: the repository is a local path, and the source bytes are read
anonymously over HTTPS (the store's config authorizes that one prefix).

```python
from load_rtofs import open_rtofs      # rtofs/load_rtofs.py, standalone

best = open_rtofs("best", repo_path="../rtofs_icechunks_v1")   # lazy Dask arrays
gulf = best.sel(time="2026-11-20").isel(Depth=0, Y=slice(1733, 1912), X=slice(2349, 2573))
speed = (gulf.u**2 + gulf.v**2) ** 0.5

fc = open_rtofs("forecast", repo_path="../rtofs_icechunks_v1")
day3 = fc.sel(lead="3D")                                       # every run's day-3 forecast
pinned = open_rtofs("best", repo_path="../rtofs_icechunks_v1", tag="data-through-2026-11-25")
```

The grid is curvilinear, so select points by nearest cell, not with `sel(Latitude=…)`:

```python
d2 = (best.Latitude - 25.5) ** 2 + (best.Longitude + 79.9) ** 2
iy, ix = np.unravel_index(int(d2.argmin()), d2.shape)          # Y=1834, X=2574 (Florida Current)
ts = best[["u", "v"]].isel(Depth=0, Y=iy, X=ix).load()
```

Use `repo_path="../rtofs_icechunks"` instead on a Python ≥ 3.12 environment with icechunk 2.x.
`open_rtofs` works with both: icechunk 2.x authorizes anonymous HTTPS with `credentials.HttpAccess`,
1.x with `None`, and the loader picks whichever exists.

## Report: surface currents at two points (`plot_velocity_timeseries.ipynb`)

Run in the `notebook` conda environment against `rtofs_icechunks_v1/` on 2026-11-19. The notebook reads
surface `u` and `v` for every `/best` day at two grid cells and plots `u`, `v` and speed.

| Point | Cell | Days with data | Mean u | Mean v | Mean speed |
|---|---|---|---|---|---|
| Florida Current, Straits of Florida | Y=1834, X=2574 (25.51° N, 79.92° W) | 282 | +0.09 | **+1.19** | 1.20 m s-1 |
| Loop Current, eastern Gulf of Mexico | Y=1834, X=2492 (25.51° N, 86.48° W) | 282 | +0.14 | −0.22 | 0.58 m s-1 |

- The **Florida Current** is what it should be: almost purely northward (v ≈ speed), 0.3–1.9 m s-1, with
  weaker spells (late 2025-10, late 2026-10) that last days to a couple of weeks.
- The **Loop Current** point swings between strong flow (> 1 m s-1, mostly southward in summer 2025, eastward
  in autumn 2025 and late 2026-08) and near-calm, as the Loop Current extends, retreats and sheds eddies past it.
- The 2025-12-11 … 2026-08-19 gap is the archive, not the store: no runs exist there.
- Reading both points for the full record took **28–30 s** (1,128 chunk requests, `async.concurrency` 32).

## Performance

Observed from this JupyterHub with a local repository, `async.concurrency` = 32.

| Operation | v1 store, icechunk 1.1.21 (2026-11-19) | v2 store, icechunk 2.3.1 (2026-11-18) |
|---|---|---|
| Open `/best` (metadata only) | 0.3 s | 1.4 s |
| Open `/forecast` | 0.04 s | 0.1 s |
| One native chunk | ≈ 0.4–1.1 s (first read includes connection setup) | 0.36 s |
| Gulf of Mexico, 0–20 m, one day | 0.4 s (`u`) | 0.6 s (`u` and `v`) |
| Surface point, one run's 8 leads | 2.0 s | 0.5 s |
| Surface point, full `/best` record, u + v, two points | 28–30 s | not measured |

The two columns were measured on different days, under different server load, so they are not a
benchmark of v1 against v2. The data reads are dominated by TAMU-CC's server either way. Keep concurrency
modest: every reader hits one university web server, and it occasionally drops a connection ("connection
closed before message completed"). Retry; it is not corruption.

## Two stores: Icechunk format v1 and v2

On 2026-11-19 the JupyterHub `notebook` environment was found on Python 3.11 with icechunk 1.1.21, while
the store had been built the day before with icechunk 2.3.1 on Python 3.13. **icechunk 2.x requires
Python ≥ 3.12, and icechunk 1.x cannot read format v2**, so neither the RTOFS nor the TXLA store could be
opened from that environment. The RTOFS store was therefore rebuilt in format v1 from the same cached
references (`rtofs_icechunks_v1/`, 203 s). Both stores point at the same source bytes, hold the same
groups, and passed the same source validation. Use the one that matches your environment.

| | Icechunk 1.x (format v1) | Icechunk 2.x (format v2) |
|---|---|---|
| Python | 3.11+ | **3.12+** |
| Repository layout | `config.yaml`, `refs/` (one object per branch/tag), `snapshots/`, `manifests/`, `transactions/` | single `repo` object holding config, branches, tags and snapshot ancestry; previous versions backed up under `overwritten/`; no `refs/`, no `config.yaml` |
| Consistency | branch and tag updates are separate objects | the `repo` object is the single point of consistency and atomicity |
| Snapshots | each records its parent | parentage kept in the `repo` object |
| Manifests | chunk length per dimension | number of chunks per dimension (helps rectilinear grids); virtual chunk locations can be compressed |
| Compatibility | cannot read v2 repositories | reads v1; upgrades in place with `icechunk.upgrade_icechunk_repository()` (metadata only, not reversible) |
| Anonymous HTTP sources | `authorize_virtual_chunk_access={prefix: None}` | `{prefix: icechunk.credentials.HttpAccess}` |
| Python API | `ChunkType.INLINE` etc. | `ChunkType.inline` etc.; `manifest_files()` → `list_manifest_files()`; `inspect_snapshot()` returns a dict; submodules (`icechunk.config`, `.session`, `.storage`) |
| VirtualiZarr in this project | 2.4.0: `to_icechunk` has no `mode=` (the build emulates it) | 2.8.0: `to_icechunk(..., mode=)` |

Sources: [Icechunk spec v2, "changes from spec version 1"](https://icechunk.io/en/latest/reference/spec-v2/),
[breaking changes in 2.0](https://icechunk.io/en/stable/reference/breaking-changes-2/),
[migration guide](https://icechunk.io/en/stable/reference/migration-2/).

If the hub returns to Python ≥ 3.12, use `rtofs_icechunks/` and let `rtofs_icechunks_v1/` go: two stores
mean two refreshes. Converting v1 to v2 later is one `upgrade_icechunk_repository()` call; there is no
way back.

## Metadata repairs

Values are untouched except the materialized coordinates. Recorded in each group's `metadata_repairs`
attribute:

| What | Source | In the store |
|---|---|---|
| `Longitude` | 74 … 794 (modulo 360) | wrapped to [-180, 180); grid order unchanged (the tripolar grid is not rectilinear) |
| `Latitude`/`Longitude` | in every file | stored once |
| `u`, `v` | per-file `valid_range`, version-tagged `long_name`, units `m/s`, `coordinates` naming `Date` | `valid_range` and `coordinates` dropped; `long_name` "Eastward/Northward sea water velocity"; units `m s-1` |
| `Date`, `X`, `Y` index variables | `%Y%m%d.%f` day, 1…N indices | dropped (redundant) |
| time | `MT`, days since 1900-12-31 | `forecast_reference_time` / `lead` / `valid_time`, checked against `MT` in every file |
| global | `Conventions` CF-1.0 | CF-1.8 |

`experiment` is stored as a fixed-length unicode array, which zarr-python 3.1 flags as having no Zarr v3
specification yet; it reads fine with zarr-python but other Zarr libraries may not read that coordinate.

## Rebuilding and daily updates

```bash
# Python 3.11 / icechunk 1.x (the hub's notebook env today)
conda run -n notebook python rtofs/build_rtofs_icechunk.py --repo rtofs_icechunks_v1
# Python >= 3.12 / icechunk 2.x
python rtofs/build_rtofs_icechunk.py                       # writes rtofs_icechunks/
python rtofs/build_rtofs_icechunk.py --dry-run             # discover and assemble, no write
```

Each run re-lists the archive and opens only new or re-modified files: per-file references are cached in
`rtofs/.ref_cache/` (git-ignored, plain `.npz` + JSON, independent of the Icechunk version), keyed by the
listing's modification time. A refresh takes about 2–4 minutes; a first build from an empty cache about
30 minutes (≈ 2 s per file header, 4 workers). It rebuilds both groups, validates from the consumer read
path against the source (first and last run, both sides of every version or lead-count change, two random
runs, padding and excluded slots, best/forecast consistency), and tags the snapshot
`data-through-<last valid day>`. Old snapshots stay readable by ID or tag.

How a build writes, and why:

- **One array per commit, on a temporary Icechunk branch**, then `main` jumps to the final snapshot. All
  four arrays in one session (~3.7 M references) exceeded this machine's 3.9 GB memory limit.
- **Latitude/Longitude are written once** and kept while their hash (`grid_sha256`) is unchanged. A refresh
  adds ~53 MB, almost all rewritten reference manifests: ~19 GB a year if every snapshot is kept. Expiring
  old snapshots is not automated, because which tags to keep is an owner decision.

`smoke_test.ipynb` is the small end-to-end test (six runs across every transition, temporary repository).

## Tested environments

- Python 3.11.14 (hub `notebook` env), icechunk 1.1.21, virtualizarr 2.4.0, xarray 2026.2.0, zarr 3.1.5:
  `requirements-icechunk1.txt`. Built and read `rtofs_icechunks_v1/`; ran `plot_velocity_timeseries.ipynb`.
- Python 3.13.18, icechunk 2.3.1, virtualizarr 2.8.0, xarray 2026.9.0, zarr 3.4.2: `requirements.txt`.
  Built and read `rtofs_icechunks/`; ran `smoke_test.ipynb`.

## Not verified / open

- **Browser access**: the source server sent no `Access-Control-Allow-Origin` in the TXLA audit, so a
  browser viewer would likely show coordinates but no data. Not tested in a browser.
- **URL stability, rewrites and retention** of `history/` files are not confirmed with TAMU-CC; nor are the
  2025-12-11 … 2026-08-19 gap, whether fields are daily means or 00 UTC snapshots, or why only u/v are
  archived. Questions are in `source-audit.md`.
- `smoke_test.ipynb` has not been re-run on the icechunk 1.x environment; the production build's
  validation has.
- The repositories are local. Publishing one (e.g. to Source Cooperative) is a separate step; the
  references would not change.

## Provenance

- Build: `build_rtofs_icechunk.py` + `rtofs_build.py`; loader `load_rtofs.py`
- Time-series notebook: `plot_velocity_timeseries.ipynb`; smoke test: `smoke_test.ipynb`
- Source audit: `source-audit.md`; file manifest: `source_manifest.csv`
- v2 store first built 2026-11-18 (`XKG4RQMNKYYD6DM5S47G`); v1 store built 2026-11-19 (`C4GZ0VESHJM01E8Q3WJG`)
