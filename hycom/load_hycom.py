"""Open the HYCOM 2.2.99+CICE Currents virtual Icechunk store. Standalone: copy this file anywhere.

    from load_hycom import open_hycom
    best = open_hycom("best")          # 3-hourly series, newest run for each time step
    fc = open_hycom("forecast")        # every 12 UTC run x lead 0-192 h

Needs: icechunk (1.x for the format-v1 store built on Python 3.11), xarray, zarr 3. No credentials:
the repository is a local path and the data bytes are read anonymously over HTTPS from TAMU-CC.
"""

import icechunk as ic
import xarray as xr
import zarr

# Anonymous HTTPS authorization: icechunk 2.x has credentials.HttpAccess; 1.x authorizes with None.
HTTP_ACCESS = getattr(ic.credentials, "HttpAccess", None)


def open_hycom(group="best", repo_path="hycom_icechunks", branch="main", tag=None, snapshot_id=None,
               chunks=None, concurrency=32):
    """group: "best" or "forecast". Values decode to float64 m s-1 with NaN over land.

    chunks=None (default): lazy, NumPy on access; zarr fetches only the 2.94 MB chunks (1 x 1 x 327 x 4500)
    a selection touches. Do not pass chunks={}: each variable has ~2.2 M (best) to ~7.9 M (forecast)
    chunks, and the Dask graph alone exhausted a 3.9 GB kernel (2026-11-19). If you need Dask, pass
    larger chunks, e.g. chunks={"time": 8, "depth": 1, "lat": 327, "lon": 4500}, after selecting.
    Keep concurrency modest: every data read goes to one university web server."""
    zarr.config.set({"async.concurrency": concurrency})
    storage = ic.local_filesystem_storage(repo_path)
    prefixes = list((ic.Repository.fetch_config(storage).virtual_chunk_containers or {}).keys())
    # The build keeps its reference cache small (200 k) to fit its memory; a /forecast manifest holds up to
    # ~270 k references, so readers use a larger cache to avoid reloading manifests on every read.
    cfg = ic.RepositoryConfig.default()
    cfg.caching = ic.CachingConfig(num_chunk_refs=1_500_000)
    repo = ic.Repository.open(storage, config=cfg,
                              authorize_virtual_chunk_access={p: HTTP_ACCESS for p in prefixes})
    if snapshot_id:
        session = repo.readonly_session(snapshot_id=snapshot_id)
    elif tag:
        session = repo.readonly_session(tag=tag)
    else:
        session = repo.readonly_session(branch)
    return xr.open_zarr(session.store, group=group, consolidated=False, chunks=chunks, zarr_format=3)


if __name__ == "__main__":
    ds = open_hycom("best")
    print(ds)
    gulf = ds.isel(time=-1, depth=0).sel(lat=slice(18, 31), lon=slice(262, 279))
    print("Gulf of Mexico mean surface speed, m/s:", float(((gulf.water_u**2 + gulf.water_v**2) ** 0.5).mean()))
