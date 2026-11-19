# RTOFS history archive: source audit (2026-11-18)

Read-only audit of `https://hriai01.tamucc.edu/data/MASTR_MODELS/rtofs/history/`: directory listings, HTTP
headers, HDF5 headers and time axes. Science values were read only for the store-versus-source checks in
the smoke test and the build; no checksums were taken.

## Inventory

| | |
|---|---|
| Files | 3,666 `<run>.<valid>.<u\|v>.nc` (`YYYY-MM-DD-HH` stamps, always `-00`) in `YYYY/MM/` of the **run** month; no other files |
| Bytes | about 3.3 TB, every file 861–864 MB (listing sizes are rounded) |
| Content | Global RTOFS (HYCOM `ATLb2.00`, 1/12° tripolar), `archv2ncdf3z` output on 33 z-levels (0–5,500 m), 4500 × 3298; **only u and v**, one per file, one daily time step per file |
| Format | NetCDF-4/HDF5 (netcdf 4.7.4, hdf5 1.10.6). u/v chunks (1, 3, 413, 563), zlib level 1, no shuffle, little-endian float32, fill 1.2676506e30; 704 chunks per file |
| Runs | 266 daily 00 UTC runs, 2025-06-16 … 2026-11-16; valid days 2025-06-17 … 2026-11-24 |
| Server | Apache 2.4.52, byte ranges (HTTP 206) and ETags, `Content-Type: application/x-netcdf` |
| Growth | 16 files per day (8 leads × u, v), about 14.4 GB/day (≈ 5.3 TB/year) |

## Findings

1. **Lead length changes with the era.** Runs 2025-06-16 … 09-17 (93 runs): 5 lead days. 2025-09-18 …
   2026-08-20 (85): 8. 2026-08-21 … 08-24 (4): 4. From 2026-08-25 (84): 8. No run misses a lead inside its era.
2. **Gap: no runs from 2025-12-11 to 2026-08-19 (252 days)**, and none on 2025-06-17. 245 valid days have
   no file at all.
3. **Model version change.** Global attribute `experiment` is 93.0 up to run 2025-07-28 and 93.1 from
   2025-07-29 (binary search over the u lead-1 files). The `long_name` carries the version (`u-veloc. [93.0H]`).
4. **Two unreadable files**, both in run 2025-07-29: `2025-07-29-00.2025-08-01-00.v.nc` and
   `2025-07-29-00.2025-08-03-00.v.nc`. The attribute block of `v` fails its HDF5 metadata checksum (h5py:
   *incorrect metadata checksum after all read attempts*; netCDF-C: *NetCDF: HDF error*). The matching u
   files are fine. Found by the build, which opens every file.
5. **Grid identical** (Latitude, Longitude, Depth byte-identical in all files compared; compressed chunk
   lengths identical in every file opened by the build). Every file carries its own copy (≈ 118 MB uncompressed).
6. **Time is consistent.** `MT` (days since 1900-12-31) equals the filename valid day in every file the
   build opened; the run stamp is the real forecast start.
7. **Files rewritten in place.** All 16 files valid 2025-10-01 (runs 09-23 … 09-30) have a modification
   time of 2025-10-02 15:46, up to 9.7 days after their run.
8. **Arrival drift.** In 2025 files appeared ~4 h (leads 1–4) and ~10 h (leads 5–8) after the run; since
   2026-08 ~15.5 h. Listing times are server local time (US Central).
9. **Metadata quirks:** Longitude 74–794 with `modulo: 360`; per-file `valid_range`; `Date` with non-CF
   units `day as %Y%m%d.%f`; `Conventions: CF-1.0`; `long_name` with a leading space; units `m/s`.
10. **Empty folders:** month folders exist for all of 2025–2030; only 2025-06 … 2025-12 and 2026-08 …
    2026-11 hold files.

## Questions for TAMU

- Can the two unreadable 2025-07-29 v files be re-fetched? A rewrite changes their modification time and
  the build picks them up automatically.
- Is the 2025-12-11 … 2026-08-19 gap lost, or can it be backfilled?
- Why did runs 2026-08-21 … 08-24 keep only 4 lead days, and 2025 runs only 5?
- Are these daily means or 00 UTC snapshots? (NCEP's `3dz` daily files are daily means; the metadata here
  does not say.)
- Why only u and v (no temperature, salinity, SSH)? Intentional?
- Why were the 2025-10-01 files rewritten? Are files ever rewritten otherwise?
- How long is `history/` kept? Are the empty 2027–2030 folders intentional?
- What request load is acceptable from readers of the virtual store?
