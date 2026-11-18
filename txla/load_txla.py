"""Open the TXLA virtual Icechunk store. Standalone: copy this file anywhere.

    from load_txla import open_txla
    best = open_txla("best")          # continuous hourly series
    fc = open_txla("forecast")        # every run x lead time

Needs: icechunk>=2.3, xarray>=2026.9, zarr>=3.4. No credentials: the store is read from a
local path, and the data bytes are read anonymously over HTTPS from TAMU-CC's server.
"""

import icechunk as ic
import xarray as xr
import zarr


def open_txla(group="best", repo_path="txla_icechunks", branch="main", snapshot_id=None,
              chunks={}, concurrency=32):
    """group: "best" or "forecast". chunks={} -> lazy Dask arrays; chunks=None -> NumPy on access.
    Keep concurrency modest: every data read goes to one university web server."""
    zarr.config.set({"async.concurrency": concurrency})
    storage = ic.local_filesystem_storage(repo_path)
    # 1. read the persisted virtual chunk containers, 2. authorize each (anonymous HTTPS)
    prefixes = list((ic.Repository.fetch_config(storage).virtual_chunk_containers or {}).keys())
    repo = ic.Repository.open(
        storage, authorize_virtual_chunk_access={p: ic.credentials.HttpAccess for p in prefixes}
    )
    session = (repo.readonly_session(snapshot_id=snapshot_id) if snapshot_id
               else repo.readonly_session(branch))
    return xr.open_zarr(session.store, group=group, consolidated=False, chunks=chunks, zarr_format=3)


if __name__ == "__main__":
    ds = open_txla("best")
    print(ds)
    print(ds.temp.sel(time=ds.time[-1]).isel(Depth=0).mean().compute().item())
