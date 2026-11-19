# models_chunking

Surveys, source audits and virtual [Icechunk](https://icechunk.io) stores for ocean model output.

| Dataset | Where | Status |
|---|---|---|
| NOAA ECCOFS (`s3://noaa-nos-eccofs-pds`) | `docs/`, `handoff.md` | survey and design proposal; no store yet |
| TXLA ROMS forecast (TAMU-CC) | `txla/` | virtual store built locally (`txla_icechunks/`) |
| RTOFS global u/v, HYCOM ATLb2.00 (TAMU-CC) | `rtofs/` | virtual store built locally (`rtofs_icechunks/`) |
| HYCOM 2.2.99+CICE Currents, ESPC-D V02 (TAMU-CC) | `hycom/` | full-archive virtual store, sub-chunked NetCDF-3, built locally (`hycom_icechunks/`) |

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
