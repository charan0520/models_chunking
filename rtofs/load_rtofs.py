"""Open the RTOFS virtual Icechunk store. Standalone: copy this file anywhere.

    from load_rtofs import open_rtofs
    best = open_rtofs("best")          # one daily series, newest run for each day
    fc = open_rtofs("forecast")        # every run x lead day (1-8)

Needs: icechunk>=2.3, xarray>=2026.9, zarr>=3.4. No credentials: the store is read from a
local path, and the data bytes are read anonymously over HTTPS from TAMU-CC's server.
"""

import icechunk as ic
import xarray as xr
import zarr


def open_rtofs(group="best", repo_path="rtofs_icechunks", branch="main", tag=None, snapshot_id=None,
               chunks={}, concurrency=32):
    """group: "best" or "forecast". tag: e.g. "data-through-2026-11-25" to pin a build.
    chunks={} -> lazy Dask arrays (source chunks 3 depths x 413 x 563); chunks=None -> NumPy on access.
    Keep concurrency modest: every data read goes to one university web server."""
    zarr.config.set({"async.concurrency": concurrency})
    storage = ic.local_filesystem_storage(repo_path)
    # 1. read the persisted virtual chunk containers, 2. authorize each (anonymous HTTPS)
    prefixes = list((ic.Repository.fetch_config(storage).virtual_chunk_containers or {}).keys())
    repo = ic.Repository.open(
        storage, authorize_virtual_chunk_access={p: ic.credentials.HttpAccess for p in prefixes}
    )
    if snapshot_id:
        session = repo.readonly_session(snapshot_id=snapshot_id)
    elif tag:
        session = repo.readonly_session(tag=tag)
    else:
        session = repo.readonly_session(branch)
    return xr.open_zarr(session.store, group=group, consolidated=False, chunks=chunks, zarr_format=3)


if __name__ == "__main__":
    ds = open_rtofs("best")
    print(ds)
    gulf = ds.isel(time=slice(-1, None), Depth=slice(0, 1), Y=slice(1733, 1912), X=slice(2349, 2573))
    speed = (gulf.u ** 2 + gulf.v ** 2) ** 0.5
    print("Gulf of Mexico mean surface speed, m/s:", float(speed.mean().compute()))
