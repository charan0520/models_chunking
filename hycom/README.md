# HYCOM 2.2.99+CICE Currents

A **virtual** [Icechunk](https://icechunk.io) store of the HYCOM 2.2.99 + CICE (ESPC-D V02, FNMOC) global
ocean-current forecasts that TAMU-CC keeps at <https://hriai01.tamucc.edu/data/MASTR_MODELS/hycom/history/>.
The store holds only Zarr metadata and byte-range references (**225 MB for 44.6 TB of source files**). **The
velocity values stay in the original NetCDF-3 files on TAMU-CC's web server**, so every data read depends on
that server being up and on its files not being moved. Rewritten files are detected (see *Integrity*).

| | |
|---|---|
| Model | ESPC-D V02: HYCOM 2.2.99 + CICE 5.1.2, `expt_03.1` (FNMOC, "pre-operational"); GLBy0.08 output on GLBz0.04 latitudes |
| Variables | `water_u`, `water_v`: eastward / northward sea water velocity, m s-1 (int16 × 0.001 in the source) |
| Grid | **regular** lat/lon: `lat` 4,251 (−80 … 90°, 0.04°) × `lon` 4,500 (0 … 359.92° E, 0.08°) × `depth` 40 levels (0 … 5,000 m) |
| Coverage | 233 runs at 12 UTC, 2025-06-16 … 2026-11-15, leads 0–192 h every 3 h; `/best` 2025-06-16 12:00 … 2026-11-23 12:00 (4,201 three-hourly steps, 1,983 with no file) |
| Source | 14,574 NetCDF-3 classic files, ≈ 3.06 GB each (≈ 44.6 TB), all referenced; none excluded |
| Store | `hycom_icechunks/` (local, git-ignored), Icechunk **format v1** (icechunk 1.1.21, Python 3.11) |
| Snapshot | `DC4YJCECQEWDSAG9QC00` on `main`, tag `data-through-2026-11-23` (built 2026-11-19) |

## Groups

Each source file is **one valid time from one forecast run**, `<run>.<valid>.nc` with stamps
`YYYY-MM-DD-HH` (e.g. `2026/11/2026-11-15-12.2026-11-23-12.nc`), holding both `water_u` and `water_v`.
Both groups reference the **same** source bytes; neither copies anything.

| Group | Dimensions | Use it for |
|---|---|---|
| `/forecast` | `forecast_reference_time` (233) × `lead` (65: 0–192 h) × `depth` × `lat` × `lon` | comparing runs and lead times. `valid_time(forecast_reference_time, lead)`; `n_leads` per run. |
| `/best` | `time` (4,201, 3-hourly) × `depth` × `lat` × `lon` | one continuous series. Each step comes from the **newest run** that has it; `forecast_reference_time(time)` and `lead(time)` record which. |

- **The last 8 days of `/best` are forecasts** (leads 0–192 h of the newest run). Earlier steps use leads
  0–21 h, except across short gaps between runs, where longer leads fill in.
- **No runs from 2025-12-09 to 2026-08-19** (254 days), plus 21 gaps of 1–5 days. `/best` is NaN where no
  run covers a step (`lead` = −1 h); 1,983 of 4,201 steps, almost all in the long gap.
- **23 incomplete runs** are kept and padded to 65 leads; missing leads read as NaN (`n_leads` says how many
  are real). See `source-audit.md` for the list.
- Both producer eras are combined: the merge step that puts u and v in one file changed on 2026-08-20
  (`ncks -A` → "merged water_u …"), but the layout, encoding and grid are identical.

## Chunking: sub-chunked virtual references

The source files are NetCDF-3 classic: no compression and no internal chunking. Each velocity variable is
**one contiguous 1.53 GB block** (`40 × 4251 × 4500` big-endian int16). A plain virtual store would have one
1.53 GB chunk per variable per file, and any read would fetch all of it.

Because the bytes are uncompressed, any whole-row slice of the block is a valid byte range. The store
therefore describes every block as **1,040 smaller chunks** (520 per variable), without copying anything:

| | |
|---|---|
| Chunk (`water_u`, `water_v`) | **1 time × 1 depth × 327 lat × 4,500 lon**, int16, big-endian, no compression |
| Bytes per chunk | 2,943,000 (2.94 MB) = one byte range in the source file |
| Chunk offset | `begin` of the variable (read from each file's header) + (depth × 4251 + band × 327) × 4500 × 2 |
| Chunks per field (one variable, one time) | 40 depths × 13 latitude bands (327 × 13 = 4,251 exactly, so no chunk runs past its block) |
| References | `/forecast` 15.2 M (14,574 files × 1,040), `/best` 2.3 M (2,218 filled steps × 1,040); ≈ 17.5 M in all |
| Manifest splitting | every 8 runs (`/forecast`) and 240 steps (`/best`); a read touches only the manifests it needs |
| Materialized (copied) | coordinates only: `depth`, `lat`, `lon`, times, `n_leads` (68 kB in `chunks/`) |

The velocity data never appears in the store's `chunks/` folder: the "chunks" are the references in
`manifests/`, each a (URL, offset, length, ETag) entry. Chunks cannot span longitude only partly, because a
partial row is not contiguous in the file; a narrow region still fetches full 4,500-point rows.

## Loading

No credentials: the repository is a local path, and the source bytes are read anonymously over HTTPS.

```python
from load_hycom import open_hycom          # hycom/load_hycom.py, standalone

best = open_hycom("best", repo_path="../hycom_icechunks")
# a point time series: one 2.94 MB chunk per time step and variable
ts = best[["water_u", "water_v"]].isel(depth=0).sel(lat=25.5, lon=280.1, method="nearest") \
         .sel(time=slice("2026-10-01", None)).load()
# a regional map (longitude is 0-360 E)
gulf = best.isel(time=-1, depth=0).sel(lat=slice(18, 31), lon=slice(262, 279))

fc = open_hycom("forecast", repo_path="../hycom_icechunks")
run = fc.water_v.sel(forecast_reference_time="2026-11-15T12").isel(depth=0)   # one run, 65 leads
```

- **Use NumPy-backed reads (the loader's default, `chunks=None`).** With `chunks={}` each variable becomes a
  Dask array of 2.2 M (`/best`) to 7.9 M (`/forecast`) chunks; building that task graph killed a 3.9 GB
  Jupyter kernel. Select first, then add Dask with larger chunks if you need it.
- Values decode to float64 m s-1 (the source's `scale_factor` 0.001; quantized to 1 mm s-1), NaN over land
  (`missing_value` −30000).
- The loader uses a larger reference cache than the build (1.5 M references) so a `/forecast` manifest
  (up to ~270 k references) is loaded once, not on every read.

## Report: surface currents (`plot_currents_timeseries.ipynb`)

Run in the `notebook` conda environment on 2026-11-19. Surface `water_u`, `water_v` and speed from `/best` at
two cells in the same latitude band (so each chunk serves both points):

| Point | Cell | Mean u | Mean v | Mean speed (max) |
|---|---|---|---|---|
| Florida Current, Straits of Florida | lat 25.48° N, lon 280.08° E (79.92° W) | +0.09 | **+0.98** | 0.99 m s-1 (1.70) |
| Loop Current, eastern Gulf of Mexico | lat 25.48° N, lon 273.52° E (86.48° W) | +0.15 | −0.15 | 0.58 m s-1 (1.50) |

Daily 12 UTC values over 279 days with data. The Florida Current is almost purely northward, with several
weak spells (speed < 0.5 m s-1, one near zero in 2026-10). The Loop Current point alternates between strong
flow and near-calm as the current extends and sheds eddies. The 3-hourly view of the last 30 days shows
the sub-daily (tidal/inertial) wiggles that daily sampling misses.

| Plot | Chunk reads | Time |
|---|---|---|
| Whole record, daily 12 UTC, both points | 558 (≈ 1.6 GB) | 5.0 s |
| Last 30 days, 3-hourly, both points | 482 (≈ 1.4 GB) | 5.0 s |

## Performance

Measured 2026-11-19 from this JupyterHub, local repository, `async.concurrency` 32, NumPy-backed reads:

| Operation | Time |
|---|---|
| Open `/best` / `/forecast` (metadata) | 0.14 s / 0.04 s |
| One value (one 2.94 MB chunk) | 0.8 s |
| Gulf of Mexico surface map, one step (1–2 latitude bands) | 0.8 s |
| Global surface map, one step (13 chunks, 38 MB) | 1.3 s |
| One point, one run's 65 leads | 1.5 s |
| One point, 526 daily steps, u + v (558 chunks) | 5.2 s, peak 750 MB |

These are far better than the native layout would allow (one value would fetch 1.53 GB), but a point series
still moves 2.94 MB per time step and variable: the full 3-hourly record at one point is ≈ 4,400 chunks
(≈ 13 GB). Keep concurrency modest: every reader hits one university web server.

## Integrity

Each reference stores the source file's **ETag**. If TAMU-CC rewrites a file, reads of it fail with "the
checksum of the object owning the virtual chunk has changed" instead of returning different bytes (tested
with a deliberately wrong ETag). Rerun the build to re-reference the new version. Files modified long after
their run are common in the listing (late uploads, see `source-audit.md`), so this matters here.

## Metadata repairs

Values are untouched. Recorded in each group's `metadata_repairs` attribute:

| What | Source | In the store |
|---|---|---|
| `water_u`, `water_v` | units `m/s`; per-file `actual_range`, `NAVO_code` | units `m s-1`; those dropped; `scale_factor`, `add_offset`, fill −30000 (`missing_value`) kept |
| time | `time` (hours since 2000-01-01), `tau` (hours since analysis) per file | `forecast_reference_time` / `lead` / `valid_time`, checked against the filename in every file |
| `depth`, `lat`, `lon` | in every file | stored once (identical in all 14,574 files) |
| global | `Conventions` CF-1.0 NAVO_netcdf_v1.0; per-file `history`, `created_on`, `time_origin` | CF-1.8; per-file attributes dropped |

## Rebuilding and updates

```bash
/srv/conda/envs/notebook/bin/python -u hycom/build_hycom_icechunk.py            # writes ../hycom_icechunks
/srv/conda/envs/notebook/bin/python -u hycom/build_hycom_icechunk.py --scan-only
/srv/conda/envs/notebook/bin/python -u hycom/build_hycom_icechunk.py --runs 1 --repo /tmp/hycom_smoke  # smoke test
```

1. **Discover**: walk the Apache listing of `history/` (`latest/` is ignored; its files move).
2. **Scan**: one 80 kB range request per new file reads the NetCDF-3 header, `depth`/`lat`/`lon`, `time` and
   `tau`, and checks dimensions, encoding, grid and time against the filename. Results are cached in
   `.scan_cache.jsonl` (git-ignored) keyed by the listing's modification time, so a rebuild only scans new
   or changed files. The first scan of 14,574 files took 14 minutes (6 parallel requests).
3. **Write**: coordinates and full-size empty arrays, then the virtual references in batches (10 runs or
   200 steps per commit) on a temporary Icechunk branch; `main` moves to the last commit at the end. The
   repository is reopened for every batch with small caches: with icechunk's default caches the first full
   build was killed by this machine's 3.9 GB memory limit after 100 runs; now it peaks at about 1 GB.
4. **Validate** from the consumer read path: six files (first, last, the first file after the producer
   change, three random) compared with netCDF4, the last chunk of a file (end of file), a padded lead, and
   `/best` against `/forecast`. Then tag `data-through-<last valid day>`.

A full rebuild takes about 13.5 minutes with a warm scan cache: 35 s listing and scan, 12.8 min writing
~17.5 M references, 9 s validation.
Each rebuild adds a new snapshot (old ones stay readable by ID or tag); expiring old snapshots is not
automated.

## Files

- `build_hycom_icechunk.py`: discovery, header scan, store layout, references, validation
- `load_hycom.py`: standalone loader
- `plot_currents_timeseries.ipynb`: the time-series report above
- `source-audit.md`: archive audit and plan (2026-11-19); `source_listing_2026-11-19.csv`: full listing
- `requirements.txt`: tested versions (Python 3.11.14, icechunk 1.1.21, xarray 2026.2.0, zarr 3.1.5, netCDF4 1.7.2)

## Not verified / open

- **Browser access**: the source host sends no CORS header, so browser readers will show coordinates but no data.
- **Late uploads vs rewrites**, the 254-day gap, the 23 incomplete runs and the producer change are not yet
  explained by TAMU-CC (questions in `source-audit.md`).
- **Format v1 only.** On Python ≥ 3.12 with icechunk 2.x the store can be read as is, or upgraded once
  with `icechunk.upgrade_icechunk_repository()` (not reversible; icechunk 1.x can then no longer read it).
- **Request load**: a point series is thousands of small range requests to a university server; an
  acceptable rate has not been agreed with TAMU-CC.
- The repository is local. Publishing it (e.g. to Source Cooperative) is a separate step; the references
  would not change.
