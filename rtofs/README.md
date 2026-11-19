# HYCOM ATLb2.00 Currents: surface velocity time series

`plot_velocity_timeseries.ipynb` plots daily surface currents (eastward `u`, northward `v` and speed) at
two points in the Gulf of Mexico and the Straits of Florida, read from a **virtual**
[Icechunk](https://icechunk.io) store of the global RTOFS (HYCOM `ATLb2.00`) velocity archive that TAMU-CC
keeps at <https://hriai01.tamucc.edu/data/MASTR_MODELS/rtofs/history/>.

The store (`rtofs_icechunks_v1/`, about 110 MB) holds only Zarr metadata and byte-range references.
**The velocity values stay in the original NetCDF-4 files on TAMU-CC's web server**, so the notebook
needs network access to that server, and works only while those files are not moved or rewritten.

## Running the notebook

1. Use the JupyterHub `notebook` conda environment (Python 3.11, icechunk 1.1.21). Exact versions are in
   `requirements-icechunk1.txt`:
   ```bash
   conda run -n notebook pip install -r rtofs/requirements-icechunk1.txt   # only if something is missing
   ```
2. Start the notebook from the `rtofs/` folder; it opens the store at `../rtofs_icechunks_v1`.
3. Run all cells. Reading the full record at both points takes about **30 s**; everything else is under 1 s.

No credentials are needed: the repository is a local path, and the source bytes are read anonymously over
HTTPS (the store's config authorizes that one URL prefix).

What the notebook does:

| Step | Cell | What happens |
|---|---|---|
| Open | `open_rtofs("best", repo_path="../rtofs_icechunks_v1")` | Opens the `/best` group lazily (metadata only, ≈ 0.3 s). Nothing is downloaded yet. |
| Pick cells | nearest-cell search on `Latitude`/`Longitude` | The grid is curvilinear, so `sel(Latitude=…)` does not work. Loads the two 59 MB coordinate arrays (stored in the repository, not on TAMU-CC). |
| Read | `best[["u", "v"]].isel(Depth=0, Y=iy, X=ix).load()` | One source chunk per day per variable per point: ≈ 1,100 HTTPS range requests, ≈ 1.3 GB, 28–32 s. |
| Plot 1 | full record | `u`, `v` and speed per point; gray band where the archive has no runs. |
| Plot 2 | since 2026-08-20 | Surface speed at both points plus the forecast `lead` that supplied each `/best` day. |

To look at other places, edit `POINTS = {name: (lat, lon)}` in the "Pick the grid cells" cell. Longitude
is in [-180, 180). To read a different depth, change `Depth=0` to another index (see `best.Depth`).

## Dataset

| | |
|---|---|
| Model | Global RTOFS, HYCOM `ATLb2.00`, 1/12° tripolar grid; experiments 93.0 (runs up to 2025-07-28) and 93.1 (from 2025-07-29) |
| Producer / host | NCEP model output, archived by TAMU-CC (Harte Research Institute) |
| Variables | `u`, `v`: eastward / northward sea water velocity, m s-1, float32. The archive has no temperature, salinity or SSH |
| Grid | 33 z-levels (0–5,500 m) × 3,298 `Y` × 4,500 `X`; 2-D `Latitude`/`Longitude`; longitude wrapped to [-180, 180) |
| Time (`/best`) | 527 daily 00 UTC valid days, 2025-06-17 … 2026-11-25; **245 days have no run** and read as NaN |
| Source runs | 267 daily 00 UTC forecast runs, 2025-06-16 … 2026-11-17, normally 8 lead days each |
| Source files | 3,682 NetCDF-4/HDF5 files (≈ 3.3 TB), one per run × valid day × variable (`<run>.<valid>.<u\|v>.nc`); 2 unreadable files excluded |
| Store snapshot | `C4GZ0VESHJM01E8Q3WJG` (2026-11-19), tag `data-through-2026-11-25` |

### The `/best` group (what the notebook reads)

The store has two groups over the same source bytes: `/forecast` (every run × lead 1–8 days) and `/best`.
The notebook uses `/best`, a single continuous daily series:

```
<xarray.Dataset>  Size: 2TB (virtual)
Dimensions:   time: 527, Depth: 33, Y: 3298, X: 4500
Coordinates:
  * time                     (time)   datetime64   2025-06-17 … 2026-11-25
  * Depth                    (Depth)  float32      0.0, 10.0, … 5500.0
    Latitude, Longitude      (Y, X)   float32      59 MB each, stored in the repository
    forecast_reference_time  (time)   datetime64   run that supplied each day
    lead                     (time)   timedelta64  days into that run (-1 day = no run)
    experiment               (time)   <U8          "93.0" or "93.1"
Data variables:
    u, v                     (time, Depth, Y, X) float32, chunks (1, 3, 413, 563)
```

- Each day comes from the **newest run with readable `u` and `v`**; `forecast_reference_time`, `lead` and
  `experiment` record which. Most days are lead 1.
- **The last seven days are forecasts** (leads 2–8 of the newest run, 2026-11-17). They change when newer
  runs are added. The notebook's second plot shows this.
- **No runs exist from 2025-12-11 to 2026-08-19** (nor on 2025-06-17). Those days have no chunk at all, so
  they read as NaN without any network request. This gap is in TAMU-CC's archive, not the store.

## Format

| Layer | Format |
|---|---|
| Source data | NetCDF-4 / HDF5, one variable per file, zlib level 1 compression, on TAMU-CC's HTTPS server |
| Store | Icechunk repository, **on-disk format v1** (written with icechunk 1.1.21), Zarr v3 metadata, local path `rtofs_icechunks_v1/` |
| Data chunks | *virtual*: each Zarr chunk is a reference (URL, byte offset, length) to an HDF5 chunk inside a source file; read with `numcodecs.zlib` |
| Materialized in the store | coordinates only: `Latitude`, `Longitude`, `Depth`, and the time coordinates |
| Conventions | CF-1.8 (the source files say CF-1.0) |

Format v1 is required because the `notebook` environment is Python 3.11, and icechunk 2.x (which reads
both formats) needs Python ≥ 3.12. Icechunk 1.x cannot read format v2 stores. `load_rtofs.py` works with
both: it authorizes anonymous HTTPS with `None` on 1.x and `credentials.HttpAccess` on 2.x.

Metadata cleaned up relative to the source (values untouched; recorded in the `metadata_repairs`
attribute): longitude wrapped from 74…794 to [-180, 180); `Latitude`/`Longitude` stored once instead of
in every file; `u`/`v` units `m/s` → `m s-1` with plain `long_name`s; HYCOM `MT` time (days since
1900-12-31) replaced by `forecast_reference_time`/`lead`/`valid_time`.

## Chunking

Nothing is rechunked: a virtual store can only point at the chunks the provider wrote.

| | |
|---|---|
| Chunk shape (`u`, `v`) | **1 time × 3 depths × 413 Y × 563 X**, float32 |
| Chunk size | 2.79 MB uncompressed, about **1.2 MB** compressed (zlib 1) |
| Chunks per 3-D field (one variable, one day) | 11 × 8 × 8 = **704**; last Y and X chunks are partial |
| References in `/best` | 527 days × 704 × 2 variables ≈ 0.74 M (fewer, since gap days have none) |
| Manifest splitting | `/best` references are split every 120 days, so recent data does not load the whole history's references |

What this means for the notebook:

- **A point time series is the expensive direction.** Time is the outermost axis and every chunk holds
  one day, so one surface point costs one 1.2 MB chunk per day per variable to get a single value:
  282 days × 2 variables = 564 requests per point, about 680 MB, for 564 numbers.
- Points in the same 413 × 563 tile and top three depths (0, 10, 20 m) live in the same chunks. The
  notebook's two points are in fact in the same tile (Y=1834, X=2492 and 2574 both fall in tile Y 1652–2064,
  X 2252–2814), but it loads them one at a time and nothing is cached, so each chunk is fetched twice
  (1,128 requests). Reading both points in one `isel(Y=[...], X=[...])` would halve that.
- **Maps and regional windows are cheap**: the Gulf of Mexico at 0–20 m for one day is about 8 chunks per
  variable (≈ 0.4 s).

If time series become the main use, the fix is a *materialized* time-chunked copy of a region, not a
different virtual store; the virtual layout is fixed by the provider's files.

## Results (run 2026-11-19)

| Point | Cell | Days with data | Mean u | Mean v | Mean speed |
|---|---|---|---|---|---|
| Florida Current, Straits of Florida | Y=1834, X=2574 (25.51° N, 79.92° W) | 282 | +0.09 | **+1.19** | 1.20 m s-1 |
| Loop Current, eastern Gulf of Mexico | Y=1834, X=2492 (25.51° N, 86.48° W) | 282 | +0.14 | −0.22 | 0.58 m s-1 |

- The **Florida Current** is almost purely northward (v ≈ speed), 0.3–1.9 m s-1, with weaker spells (late
  2025-10, late 2026-10) lasting days to a couple of weeks.
- The **Loop Current** point swings between strong flow (> 1 m s-1; mostly southward in summer 2025,
  eastward in autumn 2025 and late 2026-08) and near-calm, as the Loop Current extends, retreats and sheds
  eddies past it.
- Timings: open 0.3 s, cell search 0.7 s, full read 28–32 s (`async.concurrency` 32), plots < 0.5 s each.

## Caveats

- **Every data read goes to one university web server.** It occasionally drops a connection
  ("connection closed before message completed"); re-run the cell. It is not corruption. Keep
  `concurrency` modest (the loader's default is 32).
- The last seven `/best` days are forecasts, not analyses; check `lead` before treating them as such.
- Two `v` files from the 2025-07-29 run are unreadable (HDF5 checksum error) and excluded; `/best` takes
  those days from the next run, so the notebook loses nothing.
- Whether fields are daily means or 00 UTC snapshots, and how long TAMU-CC keeps the files, is not
  confirmed with TAMU-CC (questions in `source-audit.md`).
- Startup warnings are expected: icechunk's "LocalFileSystem storage is not safe for concurrent commits"
  (the notebook only reads) and zarr's "Numcodecs codecs are not in the Zarr version 3 specification".

## Files

| File | Role |
|---|---|
| `plot_velocity_timeseries.ipynb` | this notebook |
| `load_rtofs.py` | standalone loader, `open_rtofs(group, repo_path, tag=…, snapshot_id=…)` |
| `requirements-icechunk1.txt` | tested environment (Python 3.11.14, icechunk 1.1.21, xarray 2026.2.0, zarr 3.1.5) |
| `build_rtofs_icechunk.py`, `rtofs_build.py` | build / daily refresh of the store: `conda run -n notebook python rtofs/build_rtofs_icechunk.py --repo rtofs_icechunks_v1` (≈ 2–4 min) |
| `source-audit.md`, `source_manifest.csv` | source archive audit and per-file manifest |
