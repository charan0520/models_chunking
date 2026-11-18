# RTOFS virtual Icechunk store

A **virtual** [Icechunk](https://icechunk.io) store of the global RTOFS ocean-velocity archive that TAMU-CC
keeps at <https://hriai01.tamucc.edu/data/MASTR_MODELS/rtofs/history/>. The store holds only Zarr
metadata and byte-range references. **Every data value is read from TAMU-CC's server when you ask
for it**, so reads depend on that server being up and on its files staying unchanged.

| | |
|---|---|
| Model | Global RTOFS (HYCOM `ATLb2.00`, 1/12° tripolar), experiments 93.0 and 93.1 |
| Variables | `u`, `v` (eastward/northward sea water velocity, m s-1); no temperature, salinity or SSH in the source |
| Grid | 33 z-levels 0–5,500 m × 3298 (Y) × 4500 (X); 2-D `Latitude`/`Longitude`, longitude in [-180, 180) |
| Coverage | 267 daily 00 UTC runs, 2025-06-16 … 2026-11-17; `/best` valid days 2025-06-17 … 2026-11-25 (527 days, 245 with no run). As built 2026-11-18; refresh daily |
| Store | `rtofs_icechunks/` (local, git-ignored), branch `main`, tag `data-through-<last valid day>` per build |
| Source | 3,680 NetCDF-4 files (≈ 3.3 TB) referenced in place; 2 unreadable files excluded. Store itself: ≈ 110 MB |

## Groups

Both groups reference the same source bytes; neither copies data.

- **`/forecast`**: dims `(forecast_reference_time, lead, Depth, Y, X)`. Every run, with `lead` 1–8 days.
  Coordinates: `valid_time(forecast_reference_time, lead)`; per run, `experiment` (`93.0`/`93.1`) and
  `n_leads` (how many leads the archive holds).
- **`/best`**: dims `(time, Depth, Y, X)`. One field per valid day (00 UTC), taken from the **newest run
  with readable u and v** for that day. Per-day coordinates: `forecast_reference_time`, `lead` (−1 day when
  no run covers the day) and `experiment`. The axis is continuous and daily; days with no run read as NaN.

The groups exist for the same reasons as in the TXLA store: forecast verification needs run × lead, and
most other uses want one time series.

## Load it

```python
from load_rtofs import open_rtofs      # rtofs/load_rtofs.py, standalone

best = open_rtofs("best")              # lazy (Dask); chunks=None gives NumPy on access
gulf = best.sel(time="2026-11-20").isel(Depth=0, Y=slice(1733, 1912), X=slice(2349, 2573))
speed = (gulf.u**2 + gulf.v**2) ** 0.5

fc = open_rtofs("forecast")
day3 = fc.sel(lead="3D")               # every run's day-3 forecast
pinned = open_rtofs("best", tag="data-through-2026-11-25")
```

Two separate access steps, both anonymous:

1. **Repository metadata**: a local path (`ic.local_filesystem_storage`). No credentials.
2. **Source data**: anonymous HTTPS to `https://hriai01.tamucc.edu/data/MASTR_MODELS/rtofs/history/`.
   The prefix is stored in the repository config and must be authorized explicitly
   (`ic.credentials.HttpAccess`); `open_rtofs` does both.

Tested with the versions in `requirements.txt` (icechunk 2.3.1, virtualizarr 2.8.0, xarray 2026.9.0,
zarr 3.4.2, Python 3.13).

## Decisions (owner, 2026-11-18)

- **Shorter runs are padded to 8 leads.** 2025-06-16 … 09-17 runs have 5 lead days and 2026-08-21 … 08-24
  have 4; their missing leads have no reference and read as NaN (`n_leads` says how many are real).
- **Both model versions kept**, tagged per run by `experiment`. Filter with
  `fc.where(fc.experiment == "93.1", drop=True)`.
- **Longitude wrapped to [-180, 180)**; grid order unchanged (the tripolar grid is not rectilinear).
- References point at TAMU-CC over HTTPS directly; the repository is local in this repo.

## Known gaps and excluded files

- **No runs from 2025-12-11 to 2026-08-19** (and none on 2025-06-17): 245 days of `/best` are NaN.
- **Unreadable source files, excluded:** `2025/07/2025-07-29-00.2025-08-01-00.v.nc` and `2025/07/2025-07-29-00.2025-08-03-00.v.nc`. Both fail HDF5's metadata checksum in h5py and
  netCDF-C. In `/forecast` that run's v at those leads is NaN; `/best` takes those days from the next run,
  so `/best` loses nothing. They are retried automatically if their modification time changes.
- `source_manifest.csv` lists every discovered file and what the build did with it.

## Metadata repairs

Values are untouched except the materialized coordinates. Recorded in each group's `metadata_repairs`
attribute: longitude wrapped; lat/lon stored once; u/v lose the per-file `valid_range` and the
`coordinates` attribute, get version-free `long_name`s and units `m s-1`; `Date`, `X`, `Y` index
variables dropped; time rebuilt as `forecast_reference_time`/`lead`/`valid_time` (checked against `MT`
in every file); `Conventions` CF-1.8.

## Performance

Source chunks are **3 depths × 413 × 563** (about 1.2 MB compressed), zlib level 1. Every read fetches
whole chunks from TAMU-CC. Observed from this JupyterHub, `async.concurrency=32`, local repository:

| Read | Time |
|---|---|
| Metadata open of a group | 1.4 s (`/best`), 0.1 s (`/forecast`) |
| One native chunk | 0.36 s |
| Gulf of Mexico, 0–20 m, u and v, one day (8 chunks each) | 0.6 s |
| Global surface map, one variable (88 chunks, ≈ 100 MB) | 2.2 s |
| Surface point, 8 days | 0.5 s |

A point time series fetches one 1.2 MB chunk per day (and per 3 depths), so a year at one point is about
365 requests and 440 MB; a full 3-D global field is 704 chunks (≈ 860 MB). That is the source layout and
cannot be changed without republishing the files. Keep concurrency modest (32 here): every reader hits
one university web server.

Browser access is **untested**. The source host sent no CORS header in the TXLA audit, so a browser
viewer would likely show coordinates but no data.

## Build and refresh

```bash
python rtofs/build_rtofs_icechunk.py            # first run opens every file (~30-45 min); later runs only new ones
python rtofs/build_rtofs_icechunk.py --dry-run  # discover and assemble, no write
```

The first build took 29 minutes (3,596 header reads at 4 workers); a refresh takes about 2 minutes.
Each run re-lists the archive and opens only new or re-modified files (per-file references are cached in
`rtofs/.ref_cache/`, git-ignored, keyed by the listing's modification time). It rebuilds both groups,
commits once, validates from the consumer read path against the source (first and last run, both sides of
every version or lead-count change, two random runs, padding and excluded slots, best/forecast
consistency), and tags the snapshot. Old snapshots stay readable by ID or tag.

How a build writes, and why (both learned on 2026-11-18):

- **One array per commit, on a temporary Icechunk branch**, then `main` jumps to the final snapshot. All
  four arrays in one session (~3M chunk references) exceeded this machine's 3.9 GB memory limit.
- **Latitude/Longitude are written once** and kept while their hash (`grid_sha256` group attribute) is
  unchanged. Rewriting them each time added ~160 MB per refresh; a refresh now adds ~53 MB, almost all
  rewritten reference manifests. That is ~19 GB a year if every snapshot is kept; expiring old snapshots
  (`repo.expire_snapshots` + `repo.garbage_collect`) is not automated, because tags keep snapshots alive and
  which ones to keep is an owner decision.
- **Source reads are retried** in validation: TAMU-CC occasionally drops a connection ("connection closed
  before message completed"); readers can see the same error and should simply retry.

`smoke_test.ipynb` is the small end-to-end test (six runs across every transition, temporary repository).

## Provenance

- Build: `rtofs/build_rtofs_icechunk.py` + `rtofs/rtofs_build.py`; smoke test `rtofs/smoke_test.ipynb`
- Source audit: `rtofs/source-audit.md` (findings and questions for TAMU-CC)
- File manifest: `rtofs/source_manifest.csv`
- First build 2026-11-18: snapshot `XKG4RQMNKYYD6DM5S47G` on `main`, tag `data-through-2026-11-25`
