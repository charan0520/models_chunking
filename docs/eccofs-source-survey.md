# ECCOFS source survey

Survey of the NOAA NOS East Coast Coastal Ocean Forecast System (ECCOFS) archive on AWS Open Data,
done 2026-11-17 to plan virtual Icechunk stores. Only file metadata (headers) was read, no data.

- Registry: <https://registry.opendata.aws/noaa-nos-eccofs/>
- Bucket: `s3://noaa-nos-eccofs-pds` (us-east-1, anonymous / `--no-sign-request`)
- HTTPS: `https://noaa-nos-eccofs-pds.s3.amazonaws.com/<key>`
- Byte-range reads: **HTTP 206 confirmed on every prefix**. Virtual references are possible for all products.
- The bucket's `index.html` is only an S3 file browser with no documentation.

## Model

ROMS 4.3 (`title: ECCOFS forward run`, `Conventions: CF-1.4, SGRID-0.3`).
Curvilinear grid with `eta_rho × xi_rho = 1443 × 1667`, staggered u/v/psi grids, 50 `s_rho` levels and 51 `s_w` levels.
Time is `ocean_time`, in `seconds since 2011-01-01`.

## Snapshot of the bucket (2026-11-17)

About 17 TB in 3,771 files. 148 daily forecast cycles from **2026-06-08 to 2026-11-13**.

Missing cycles: 2026-06-15, 06-20, 07-08, 07-19, 07-20, 07-23, 07-26, 08-10, 08-11, 08-12, 08-13.

| Prefix | Content | Format | Files / cycle | Steps | Total |
|---|---|---|---|---|---|
| `avg/` | Daily-mean 3D state (u, v, w, temp, salt, rho, p/r-vorticity, AKv/AKt/AKs, surface fluxes, ohc, mld) | NetCDF4/HDF5, **uncompressed**, chunks `(1,50,145,167)` 3D and `(1,481,556)` 2D (about 4.8 MB and 1 MB) | 8 (`_0001`–`_0008`) | 1 per file, 12Z, days 1–8 | 1184 files, 9.0 TB |
| `his/` | Instantaneous daily 3D snapshots (similar to avg, plus Pair, EminusP, bustr/bvstr; no vorticity/ohc/mld) | **NetCDF3 64-bit offset** | 9 (`_0001`–`_0009`) | 1 per file, 00Z, days 0–8 | 1332 files, 6.2 TB |
| `qck/` | 3-hourly quicksave: zeta, rotated barotropic and surface currents, temp/salt at the surface and 3 `z_slice` depths | NetCDF3 64-bit offset | Up to 2026-07-25: 1 daily file of 65 steps, plus `_fwd` (41 steps, 7 cycles only). From 2026-07-27: 8 files (`_0001` has 9 steps, the rest 8) | 3-hourly over 8 days | 897 files, 2.0 TB |
| `sta/` | Station time series (profiles and surface fields) | NetCDF3 64-bit offset | 1 | 193 (hourly, 8 days) | 106 files, 16 GB, from 2026-07-27 |
| `frc/` | HRRR 4 km atmospheric forcing: Pair, Qair, Tair, Uwind, Vwind, rain, swrad, lwrad, lwrad_down (one variable per file) on a regular `2440 × 3680` lat/lon grid | NetCDF4, zlib+shuffle int16, chunks `(1,1220,1840)` | 9 | 121 (hourly, 5 days) | 189 files, 174 GB |
| `obs/` | CMEMS SSH/SST observations prepared for ROMS 4D-Var (flat `datum` list, about 550k obs) | NetCDF4 | 2 (`_3km` variant; first 2 MB byte-identical to the plain file) | 3-day window | 42 files, 2 GB |
| `riv/` | River forcing for 145 rivers (transport, temp, salt) | NetCDF4 | 1 | 11 | 21 files, <1 MB |

### Time layout (forecast collection)

Each daily cycle `YYYYMMDD` is an **8-day forecast**:

- `his_YYYYMMDD_000N`: snapshot at cycle + (N−1) days 00Z, for N = 1..9.
- `avg_YYYYMMDD_000N`: mean centred at cycle + (N−1) days 12Z, for N = 1..8.
- `qck_YYYYMMDD_000N` (new layout): 3-hourly from day N−1 (03Z onward) to day N 00Z. `_0001` also includes the 00Z initial step.
- `sta_YYYYMMDD`: hourly from cycle 00Z to +8 days.

Consecutive cycles overlap by 7 days. Each valid time is forecast up to 8 times, which is the classic FMRC structure.

### Change on 2026-07-27

- The `qck` file layout changes from daily to split files. There is no `qck` file at all for 07-26, which is a missing cycle.
- The `sta` file for 07-27 is the first one. Files for 07-27 to 08-02 have 96 stations and 8 variables. From 08-03 there are 330 stations and about 35 variables.
- The `avg` file size changes slightly (7592147450 → 7592146416 bytes). **Not yet checked whether the layout or variables differ.**
- `his` file sizes vary by a few bytes across the period, probably from header strings. Not yet checked.

### Retention suspicion

`frc/`, `obs/` and `riv/` all begin on 2026-10-24, exactly 21 days of cycles before the latest one. The outputs go back to June.
This looks like a rolling deletion window for inputs. If so, virtual references to those files would break. **Unconfirmed.** Re-list the bucket on a later day to check.

## Read-performance notes

Virtual stores keep the source chunking. There is no rechunking.

- `avg/`: good chunk sizes for maps and moderate for point time series, at 1 chunk per file per tile. This is the best candidate.
- `his/`: NetCDF3 with **one record per file**. Each 3D variable is a single contiguous block of about 480 MB (`50×1443×1667×4` bytes), which is one virtual chunk. Any read touching a variable pulls 480 MB. Check whether the VirtualiZarr NetCDF3 parser can sub-split contiguous blocks; if it can't, warn readers.
- `qck/`: NetCDF3 record variables, so one chunk per record per variable: 9.6 MB for 2D, 29 MB for `z_slice`. Acceptable.
- `sta/`: small, and one chunk per record, so many tiny chunks. Point series need many requests unless the coordinate and data are handled carefully.
- Every `avg`/`his` file has 1 time step, so the time coordinate must be loaded and written as one chunk (see the skill's performance-tuning notes).

## Environment versions (survey machine)

Python 3.13.18 · virtualizarr 2.8.0 · icechunk 2.3.1 · zarr 3.4.2 · xarray 2026.9.0 · netCDF4 1.7.5 · h5py 3.16.0 · s3fs 2026.9.0 · obstore 0.12.1

## How the survey was done

- `aws s3 ls --no-sign-request s3://noaa-nos-eccofs-pds/ --recursive` for the inventory.
- `curl -r 0-7` on each prefix to check the magic bytes (`\x89HDF` vs `CDF\x02`) and HTTP 206.
- Headers read with `netCDF4.Dataset("https://...nc#mode=bytes")` and `h5py` over `s3fs`.
