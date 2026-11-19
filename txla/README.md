# TXLA virtual Icechunk store

A **virtual** [Icechunk](https://icechunk.io) store of the TXLA ROMS forecast archive (Texas–Louisiana
shelf, Physical Oceanography Numerical Group, Texas A&M). The store holds only Zarr metadata and byte-range
references (about 5 MB). **The science data stays in the original NetCDF files on TAMU-CC's web server**
(`https://hriai01.tamucc.edu/data/MASTR_MODELS/txla/history/`), so every data read depends on that server
being up and on its files not being moved or rewritten.

| | |
|---|---|
| Repository | `txla_icechunks/` (committed to this repo, format v2; rebuilt by `build_txla_icechunk.py`) |
| Source | 69 NetCDF-3 classic files, 662.4 GB, counted 2026-11-18 |
| Coverage | runs starting 2026-08-23 → 2026-11-16, valid times 2026-08-23 01:00 → 2026-11-20 00:00 UTC |
| Grid | 670 × 190 curvilinear (`lon`, `lat` 2-D), 28 fixed depths 1–1000 m |
| Variables | 2-D: `zeta air_u air_v sustr svstr`; 3-D: `u v temp salt O2 pH omega` (float32) |
| First snapshot | `5QHCK61G0YSM4YK8HB40` on `main`, 2026-11-18 |

## Groups

Each source file is one forecast run: 96 hourly records starting at 01:00 UTC, issued about once a day, so
runs overlap by up to three days. Both groups reference the **same** source bytes; neither copies anything.

| Group | Dimensions | Use it for |
|---|---|---|
| `/forecast` | `forecast_reference_time` (67) × `lead` (1–96 h) × [`Depth`] × `Y` × `X` | comparing runs, forecast skill by lead time. `valid_time(forecast_reference_time, lead)` gives the clock time. |
| `/best` | `time` (2,136 hourly) × [`Depth`] × `Y` × `X` | a single continuous series. Each hour is taken from the **newest run** that covers it; `forecast_reference_time(time)` and `lead(time)` record which. |

Two groups rather than one because the layouts answer different questions and a user should not have to
assemble the best series themselves. This is deliberate; don't merge them.

- `forecast_reference_time` is the first record minus 1 h (always 00:00 UTC). It is **not** the date in the
  filename: the filename is the UTC time the file was archived, often the next day.
- Short runs are kept. Their missing leads read as NaN (no chunk is stored): `2026-08-28` has 42 records,
  `2026-09-04` has 92.
- 19 start days have no run (e.g. 08-31, 10-26, 10-27). `/best` still has no gaps, because no gap between
  runs exceeds 72 h, so it uses longer leads there; check `lead` if that matters.

### Excluded source files

When two files have the same forecast start, the **later-archived file wins** (owner decision, 2026-11-18):

| Excluded | Reason |
|---|---|
| `2026/08/…2026-08-30-114753.nc` | 1-record run; superseded by `2026-08-31-171807` (full run, same start) |
| `2026/09/…2026-09-02-001301.nc` | superseded by `2026-09-02-215634` (identical time range; rerun or correction, not confirmed with TAMU) |

The full list with the decision per file is in `source_manifest.csv`, and in each group's
`excluded_source_files` attribute.

## Loading

No credentials are needed for either half: the repository is a local path, and the source bytes are read
anonymously over HTTPS (the store's config authorizes that one prefix with `HttpAccess`).

```python
from load_txla import open_txla        # txla/load_txla.py, standalone

best = open_txla("best")               # chunks={} -> lazy Dask arrays
sst = best.temp.isel(Depth=0).sel(time="2026-11-01T12:00").load()

fc = open_txla("forecast")
run = fc.zeta.sel(forecast_reference_time="2026-11-10")   # one 96-hour run
```

Pass `chunks=None` for NumPy on access, or `snapshot_id=` to read an earlier build.

## Performance (measured 2026-11-18, from a Jupyter hub, `async.concurrency` = 32)

Every chunk is **one variable at one hour**, exactly as it lies in the NetCDF-3 file: 14.3 MB for a 3-D
variable (28 × 190 × 670), 0.5 MB for a 2-D one, uncompressed, big-endian.

| Operation | Time |
|---|---|
| Open repository + group (metadata only) | 0.5 s |
| One 3-D field, one hour (14.3 MB) | about 1 s |
| `zeta` at one point, 144 hours | 2.4 s |
| Surface `temp` at one point, 24 hours | 2.5 s (fetches 24 full 3-D fields, 340 MB) |

**Point time series of 3-D variables are expensive and cannot be made cheaper without rewriting the data:**
one point over the full record of `temp` fetches 2,136 × 14.3 MB ≈ 30 GB. Maps and short windows are cheap.
Keep concurrency modest (the loader uses 32): all data reads go to one university web server.

## Metadata repairs

Only metadata is changed; the bytes are untouched. Applied in `txla_build.py` (`ATTR_REPAIRS`):

| Variable | Source | In the store | Why |
|---|---|---|---|
| `sustr`, `svstr` | units `N s-2`, standard_name `eastward_stress`/`northward_stress` | `N m-2`, `surface_downward_eastward_stress`/`…northward…` | wrong unit; not CF names |
| `O2` | long_name "Bottom oxygen concentration", standard_name `dissolved_oxygen` | "Dissolved oxygen concentration", `mass_concentration_of_oxygen_in_sea_water` | field is 3-D; not a CF name (mg l-1 is convertible to the canonical kg m-3) |
| `pH` | standard_name `potential_hydrogen`, units `no unit ` | removed, `1` | not CF; the pH scale is not stated, so no CF pH name is assumed |
| `omega` | standard_name `carbonate_saturation_aragonite`, units `" "` | removed, `1` | not CF |
| `Depth` | | `axis: Z` | |
| all | `_CoordinateAxes`, `field`, `time`, `coordinates` | removed | `coordinates` named a nonexistent `depth`; xarray manages coordinates itself |
| global | `history`, `type` | removed | per-file; `type` claimed NETCDF3_CLASSIC |

Standard names were checked against CF standard name table v95. `zeta`'s `sea_surface_elevation` is a valid
CF alias and is kept.

## Rebuilding and daily updates

```bash
python txla/build_txla_icechunk.py            # writes ../txla_icechunks, commits a snapshot
python txla/build_txla_icechunk.py --dry-run  # discover and assemble only
```

Each run re-lists the archive, reads every file header (about 2 MB each, four at a time, under a minute),
rebuilds both groups and commits **one new snapshot**. Earlier snapshots stay readable by ID. A full rebuild is
the update method on purpose: each new run changes the last three days of `/best`. A new file appears in
`history/` the night after its run (the newest run sits in `txla/latest/` first).
The script then compares three random fields and the newest `/best` hour with the source files.

`smoke_test.ipynb` is the small end-to-end test (4 files, temporary repository, deleted afterwards).

## Tested environment

Python 3.13.18, icechunk 2.3.1, virtualizarr 2.8.0, xarray 2026.9.0, zarr 3.4.2, obstore 0.12.1,
obspec-utils 0.9.0, netCDF4 1.7.5 (`requirements.txt`).

## Not verified / open

- **Browser access**: the source server sends no `Access-Control-Allow-Origin`, so browser-based readers
  will see coordinates but no data. Not tested in a browser.
- **URL stability, rewrites and retention** of `history/` files are not confirmed with TAMU. A rewritten
  file silently changes what the store returns; a moved file breaks it. See `source-audit.md` for the
  questions to put to the provider.
- The repository is local. Publishing it (e.g. to Source Cooperative) is a separate step; the references
  would not change.
