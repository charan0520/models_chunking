# HYCOM 2.2.99+CICE Currents: source audit (2026-11-19)

Read-only audit of `https://hriai01.tamucc.edu/data/MASTR_MODELS/hycom/` (`history/` and `latest/`):
directory listings, HTTP headers, NetCDF-3 headers of 9 files chosen across every transition, and two
single-row byte-range reads (9 kB each) to prove the byte layout. No other data values were read; no
checksums were taken. The full listing is in `source_listing_2026-11-19.csv`.

## Inventory

| | |
|---|---|
| Files | 14,574 in `history/YYYY/MM/` (month of the **run**) + 65 in `latest/` (the newest run, not yet in `history/`). Name `<run>.<valid>.nc`, stamps `YYYY-MM-DD-HH`; no other files |
| Bytes | **≈ 44.6 TB**; every file 3,060,793,324 – 3,060,793,344 bytes (the listing's "2.8G"/"2.9G" is rounding of a 20-byte difference) |
| Model | ESPC-D V02 (FNMOC): HYCOM 2.2.99 + CICE 5.1.2, `expt_03.1`, "pre-operational"; `source` "HYCOM archive file, GLBz0.04", `grid_name` glby0.08 |
| Content | `water_u`, `water_v` (eastward/northward sea water velocity) **in the same file**; one valid time per file; `field_type: instantaneous` |
| Grid | **Regular** lat/lon: `lat` 4,251 (-80 … 90°, 0.04°), `lon` 4,500 (0 … 359.92°, 0.08°), `depth` 40 z-levels (0 … 5,000 m) |
| Runs | 233 runs, all **12 UTC**, 2025-06-16 … 2026-11-15 (+ 2026-11-16 in `latest/`) |
| Leads | `tau` 0 … 192 h every **3 h** (65 files per complete run); 210 runs complete, 23 incomplete |
| Valid times | 2,218 distinct, 2025-06-16 12:00 … 2026-11-23 12:00 |
| Server | Apache 2.4.52 (same host as RTOFS/TXLA); byte ranges honored (**HTTP 206**), ETags, `Content-Type: application/x-netcdf`; **no CORS header** |
| Growth | 65 files ≈ **199 GB per run** (≈ 73 TB/year at one run a day) |

## File format and byte layout

Every file is **NetCDF-3 classic (CDF-1)**: magic `CDF\x01`, header ≈ 3 kB, no compression, no internal
chunking, big-endian. `numrecs` = 1 (`time` is the unlimited dimension).

| Variable | Type | Dims | Begin (2025 file / 2026 file) | Size |
|---|---|---|---|---|
| `depth` | f8 | depth | 2,988 / 2,980 | 320 |
| `lat` | f8 | lat | 3,308 / 3,300 | 34,008 |
| `lon` | f8 | lon | 37,316 / 37,308 | 36,000 |
| `time` | f8 | time | 73,316 / 73,308 | 8 |
| `tau` | f8 | time | 73,324 / 73,316 | 8 |
| **`water_v`** | **i2** | time, depth, lat, lon | 73,332 / 73,324 | **1,530,360,000** |
| **`water_u`** | **i2** | time, depth, lat, lon | 1,530,433,332 / 1,530,433,324 | **1,530,360,000** |

- `water_u`/`water_v`: int16, `scale_factor` 0.001, `add_offset` 0, `_FillValue` = `missing_value` = −30000,
  units `m/s`, CF standard names. Per-file `actual_range`.
- Offsets shift by a few bytes between files (the header holds per-file attribute text), so **byte offsets
  must be computed per file**, from its header (≈ 3 kB range read).
- Because there is a single record, each velocity variable is **one contiguous 1.53 GB block**, laid out
  depth → lat → lon. Verified: a 4,500-value row read by byte range equals netCDF4's read of the same row
  (both eras).
- `time`: hours since 2000-01-01; `tau`: hours since analysis, whose `time_origin` attribute is the run
  time. `time` − `tau` equals the run stamp in the filename in every file inspected.

## Findings

1. **One variable = one 1.53 GB contiguous block, uncompressed.** A plain virtual store (what
   VirtualiZarr's `NetCDF3Parser` produces) has **one chunk per variable per file**: reading one value
   fetches 1.53 GB. This is unusable for anything but whole-globe 3-D reads.
2. **Sub-chunking is possible here** (unlike RTOFS, whose chunks are zlib-compressed). Uncompressed bytes
   can be addressed at any boundary, so a virtual store can describe smaller chunks as byte ranges into the
   same block, provided each chunk is contiguous in the file: whole longitude rows (`lon` = 4,500). See
   *Plan* below.
3. **Two producers, same layout.** 2025 files: `history` "ncks -A … /tile1/hycom_expt93.0-complete/.stage/…",
   global `NCO` 4.4.7. From 2026-08-20: `history` "merged water_u from
   US058GCOM-OPSnce.espc-d-031-hycom_fcst_glby008_<run>_t<tau>_u3z.nc into …v3z.nc", no `NCO` attribute.
   Dimensions, dtypes, variable attributes and grid are identical across all 9 files compared. The 2025
   staging path says `expt93.0` while `generating_model` says `expt_03.1`; probably TAMU's folder name only.
4. **Long gap: no runs from 2025-12-09 to 2026-08-19 (254 days).** 21 shorter gaps of 1–5 days. Leads of
   8 days cover most short gaps in a best-time-series; 1,983 of 4,201 three-hourly slots between the first
   and last valid time have no file, almost all inside the long gap.
5. **23 incomplete runs**, missing leads in three shapes: truncated tails (e.g. 37 files, leads 0–108 h:
   2025-06-28, 09-15, 09-19, 11-05, 12-08), missing heads (2025-09-11 has no leads 0–36 h; 2026-10-19 no
   lead 0), and holes in the middle (2025-06-27, 2026-08-25, 08-27, 09-09, 09-13, 10-12, 10-13, 10-21, 10-23).
   No duplicate (run, valid) pairs.
6. **Uploads are slow and sometimes days late.** Median first file of a run appeared ~21 h after the run
   in 2025, ~30 h since 2026-08, and **4–10 days** for runs 2026-08-21, 08-31 and 10-19 … 10-31. Within 23
   runs the files' modification times spread over more than 24 h (up to 8 days). A single listing cannot
   tell late uploads from rewrites; the pattern (one run's files trickling in, then the next run's) looks
   like transfer backlog, but this is unconfirmed. Listing times are server local time (US Central).
7. **Grid quirks:** `lat` and `lon` are float64 holding float32-rounded values (spacing 0.039993 … 0.040001°);
   `lon` is 0 … 360 with `modulo: 360 degrees`. `lat` spacing is 0.04° while `grid_name` says 0.08: the
   output is the GLBy0.08 grid interpolated to GLBz0.04 latitudes (from `source`).
8. **Metadata quirks:** `Conventions: CF-1.0 NAVO_netcdf_v1.0`; units `m/s`; per-file `actual_range`
   and `created_on`; `tau.time_origin` changes per run; empty classification attributes
   (`classification_level: ''`).
9. **Empty folders:** month folders exist for all of 2025–2030; only 2025-06 … 2025-12 and 2026-08 …
   2026-11 hold files (same as RTOFS).
10. **`latest/`** holds only the newest run (2026-11-16, 65 files), none of which is in `history/` yet. Like
    TXLA and RTOFS, reference `history/` only: `latest/` files move.

## Plan

*Built 2026-11-19 with the defaults below (owner asked for the full archive): 327-row sub-chunks, both
groups, full archive, longitude kept 0 … 360, incomplete runs padded. See `README.md` for the result.*

**Access.** Source: anonymous HTTPS to `https://hriai01.tamucc.edu/data/MASTR_MODELS/hycom/history/`,
authorized as one virtual chunk container. Destination: a local repository like `rtofs_icechunks_v1/`
until a publishing target is chosen. Discovery: the Apache listing, as for RTOFS.

**Parser.** Not `NetCDF3Parser` as-is: it gives one 1.53 GB chunk per variable. Instead, read each file's
~3 kB header (one range request), take `begin` for `water_u`/`water_v`, and write the chunk manifest
directly (offset = begin + chunk index × chunk bytes), the same way the RTOFS build assembles its manifests.
Zarr codec: `bytes` big-endian, no compressor; keep int16 with `scale_factor`/`_FillValue` attributes so
readers decode to float. Coordinates (`depth`, `lat`, `lon`, times) are loaded and stored once.

**Groups.** As RTOFS: `/forecast` (`forecast_reference_time` × `lead` 0–192 h every 3 h × depth × lat × lon)
and `/best` (3-hourly `time`, each step from the newest run that has it). Missing leads read as NaN.

**Sub-chunk size: needs a decision.** Each chunk must be whole rows of one depth level. Row counts that
divide 4,251 avoid a final chunk running past the end of the file:

| Chunk (time, depth, lat, lon) | Bytes per chunk | Chunks per file (u + v) | References, all 14,574 files, one group | One point, one time step fetches |
|---|---|---|---|---|
| native: 1 × 40 × 4251 × 4500 | 1.53 GB | 2 | 29 k | 1.53 GB |
| 1 × 1 × 4251 × 4500 (one level) | 38.3 MB | 80 | 1.2 M | 38.3 MB |
| **1 × 1 × 327 × 4500** | **2.94 MB** | 1,040 | **15.2 M** | **2.94 MB** |
| 1 × 1 × 109 × 4500 | 0.98 MB | 3,120 | 45.5 M | 0.98 MB |

My recommendation is **1 × 1 × 327 × 4500** (13 latitude bands of 13.1° per level). It is about 500×
less data per point read than the native layout, and a regional surface map (e.g. the Gulf of Mexico,
18–31° N) is 1–2 chunks per variable. The cost is reference volume: 15 M per group (30 M with `/best` and
`/forecast`), 4× RTOFS. With RTOFS, 3.7 M references already needed one-array-per-commit to fit this
machine's 3.9 GB memory, so this build would have to append in time batches, with manifest splitting
along time.

**Predicted performance.** Data comes from one university web server with no compression to save
transfer: a point time series over the full `/best` record (~2,200 three-hourly steps) at one level is
~4,400 requests × 2.94 MB ≈ 13 GB fetched. That is far better than 6.8 TB natively, but still minutes, not
seconds. Maps are cheap. Browser access will not work without CORS on the source host.

## Questions for the owner (decisions before a smoke test)

1. Sub-chunk size: 327 rows (recommended), 109 rows, or one level per chunk?
2. Both `/forecast` and `/best`, or `/best` only (halves the references)?
3. Full archive, or start with the post-gap era (2026-08-20 onward: 82 runs, 5,027 files, ≈ 15.4 TB)?
4. Keep longitude 0 … 360 (rolling to −180 … 180 is impossible without splitting rows into
   non-contiguous pieces; a reader can roll lazily)?
5. Keep incomplete runs, padded to 65 leads with NaN (as RTOFS)?

## Questions for TAMU-CC

- Are the late modification times (finding 6) uploads or rewrites? Are files ever rewritten after upload?
- Is the 2025-12-09 … 2026-08-19 gap lost, or can it be backfilled? Why are 23 runs incomplete?
- What changed in the merge step on 2026-08-20 (`ncks -A` → "merged water_u …")? Values unchanged?
- `expt93.0` in the 2025 staging path vs `expt_03.1` in `generating_model`: same experiment?
- How long is `history/` kept, at ~73 TB a year? Are the empty 2027–2030 folders intentional?
- What request load is acceptable? Sub-chunked reads mean many small range requests.
- Can CORS (`Access-Control-Allow-Origin`) be enabled for browser readers?

## Environment

Audit run 2026-11-19 from the JupyterHub `notebook` env: Python 3.11.14, netCDF4 1.7.2, numpy 2.3.5,
icechunk 1.1.21, virtualizarr 2.4.0 (has `NetCDF3Parser`), kerchunk 0.2.9, scipy 1.17.0.
