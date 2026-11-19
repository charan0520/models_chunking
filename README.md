# models_chunking

Surveys, source audits and virtual [Icechunk](https://icechunk.io) stores for ocean model output.

A virtual store holds only Zarr metadata and byte-range references; the data values stay in the original
NetCDF/HDF5 files on the provider's server, so reading a store needs network access to that server.

| Dataset | Code and docs | Store (committed) | Format | Status |
|---|---|---|---|---|
| NOAA ECCOFS (`s3://noaa-nos-eccofs-pds`) | `docs/`, `handoff.md` | none | — | survey and design proposal; no store yet |
| TXLA ROMS forecast (TAMU-CC) | `txla/` | `txla_icechunks/` (≈ 5 MB) | Icechunk format v2 (icechunk 2.x, Python ≥ 3.12) | built; SST notebook |
| RTOFS global u/v, HYCOM ATLb2.00 (TAMU-CC) | `rtofs/` | `rtofs_icechunks_v1/` (≈ 110 MB) | Icechunk format v1 (icechunk 1.x or 2.x) | built; u/v time-series notebook — **use this one** |
| | | `rtofs_icechunks/` (≈ 165 MB) | Icechunk format v2 | original 2.x build, kept for reference; superseded by `rtofs_icechunks_v1/` |
| HYCOM 2.2.99+CICE Currents, ESPC-D V02 (TAMU-CC) | `hycom/` | `hycom_icechunks/` (≈ 225 MB) | Icechunk format v1 | full archive, sub-chunked NetCDF-3; currents notebook |

## Opening a store from a clone

```python
from rtofs.load_rtofs import open_rtofs      # likewise hycom.load_hycom.open_hycom, txla.load_txla.open_txla
best = open_rtofs("best", repo_path="rtofs_icechunks_v1")
```

Each dataset folder's `README.md` covers groups, variables, build/refresh commands and caveats. Format v1
stores open with icechunk 1.x (the JupyterHub `notebook` environment, Python 3.11) and with icechunk 2.x;
format v2 stores need icechunk 2.x.

## Keeping stores up to date

The stores are snapshots of each archive at build time (see each store's `data-through-*` tag). Rebuild
with the dataset's `build_*_icechunk.py` and commit the changed store. Each RTOFS refresh adds roughly
50 MB of new snapshot files, so expire old snapshots before committing if the repository grows too large.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
