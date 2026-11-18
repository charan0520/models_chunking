"""Build a virtual Icechunk store of the TXLA (ROMS, Texas-Louisiana shelf) forecast archive.

Source : NetCDF-3 classic files on TAMU-CC's Apache server, referenced in place over HTTPS.
Store  : one Icechunk repository with two groups that reference the same source bytes
         /forecast  dims (forecast_reference_time, lead, [Depth], Y, X)  every kept run
         /best      dims (time, [Depth], Y, X)  each hour from the newest run covering it

Each source file is one forecast run of 96 hourly records. In NetCDF-3 every variable at every
record is one contiguous byte range, so every chunk is one hour of one variable and any time
layout is chunk aligned. Nothing is rewritten; only metadata is repaired (see ATTR_REPAIRS).

Used by both smoke_test.ipynb and build_txla_icechunk.py.
"""

from __future__ import annotations

import concurrent.futures as cf
import html
import logging
import re
import time
import urllib.request
from dataclasses import dataclass

import icechunk as ic
import numpy as np
import xarray as xr
from obspec_utils.registry import ObjectStoreRegistry
from obstore.store import from_url
from virtualizarr import open_virtual_dataset
from virtualizarr.manifests import ChunkManifest, ManifestArray
from virtualizarr.manifests.utils import copy_and_replace_metadata
from virtualizarr.parsers import NetCDF3Parser

log = logging.getLogger("txla")

SOURCE_PREFIX = "https://hriai01.tamucc.edu/data/MASTR_MODELS/txla/history/"  # must end in "/"
NAME_RE = re.compile(r"^txla2_his_f_latest_z\.(\d{4}-\d{2}-\d{2}-\d{6})\.nc$")
N_LEAD = 96  # hourly records per full run
FILL = np.float32(1e37)

# ---------------------------------------------------------------- discovery
_ROW = re.compile(r'<a href="([^"?/][^"]*)">')


def _get(url: str, tries: int = 4) -> str:
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # transient listing failures: retry, then raise
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise AssertionError


def discover(prefix: str = SOURCE_PREFIX) -> tuple[list[str], list[str]]:
    """Walk YYYY/MM/ folders of the Apache listing.

    Returns (sorted matching URLs, other entries that did not match the pattern)."""
    urls, other = [], []
    for y in _ROW.findall(_get(prefix)):
        y = html.unescape(y)
        if not re.fullmatch(r"\d{4}/", y):
            other.append(y)
            continue
        for m in _ROW.findall(_get(prefix + y)):
            m = html.unescape(m)
            if not re.fullmatch(r"\d{2}/", m):
                other.append(y + m)
                continue
            for f in _ROW.findall(_get(prefix + y + m)):
                f = html.unescape(f)
                (urls if NAME_RE.match(f) else other).append(prefix + y + m + f)
    return sorted(urls, key=lambda u: u.rsplit("/", 1)[1]), other


# ---------------------------------------------------------------- per-file virtual open
@dataclass
class Run:
    url: str
    archived: str  # timestamp in the filename (UTC time the file was archived)
    frt: np.datetime64  # forecast reference time = first record - 1 h
    leads: np.ndarray  # integer lead hours of each record (1..n)
    vds: xr.Dataset

    @property
    def n(self) -> int:
        return len(self.leads)


def make_registry(prefix: str = SOURCE_PREFIX) -> ObjectStoreRegistry:
    return ObjectStoreRegistry({prefix: from_url(prefix)})


def open_run(url: str, registry: ObjectStoreRegistry, load=("time", "Depth")) -> Run:
    vds = open_virtual_dataset(
        url, registry=registry, parser=NetCDF3Parser(), loadable_variables=list(load)
    )
    t = vds["time"].values.astype("datetime64[s]")
    frt = t[0] - np.timedelta64(1, "h")
    if frt != frt.astype("datetime64[D]"):
        raise ValueError(f"{url}: first record {t[0]} is not T01:00; cannot infer the run start")
    leads = ((t - frt) // np.timedelta64(1, "h")).astype(int)
    if not np.array_equal(leads, np.arange(1, len(t) + 1)) or len(t) > N_LEAD:
        raise ValueError(f"{url}: time axis is not hourly leads 1..n<=96: {leads[:5]}...")
    archived = NAME_RE.match(url.rsplit("/", 1)[1]).group(1)
    return Run(url, archived, frt.astype("datetime64[ns]"), leads, vds)


def open_runs(urls, registry, workers: int = 4) -> list[Run]:
    """Header + time reads only. A few workers: this is someone else's server."""
    with cf.ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda u: open_run(u, registry), urls))


def select_runs(runs: list[Run]) -> tuple[list[Run], list[tuple[str, str]]]:
    """One run per forecast_reference_time: the newest archived file wins (owner decision,
    2026-11-18). Returns (kept runs sorted by frt, [(excluded url, reason)])."""
    by_frt: dict = {}
    for r in runs:
        by_frt.setdefault(r.frt, []).append(r)
    kept, excluded = [], []
    for frt in sorted(by_frt):
        rs = sorted(by_frt[frt], key=lambda r: r.archived)
        kept.append(rs[-1])
        for r in rs[:-1]:
            excluded.append((r.url, f"superseded by later-archived run {rs[-1].archived} "
                                    f"for the same forecast_reference_time ({r.n} records)"))
    return kept, excluded


# ---------------------------------------------------------------- consistency checks
def check_compatible(runs: list[Run]) -> list[str]:
    """Every array must agree on everything except the record count."""
    ref = runs[0].vds
    data_vars = [v for v in ref.data_vars]
    for r in runs[1:]:
        if sorted(r.vds.data_vars) != sorted(data_vars):
            raise ValueError(f"{r.url}: variable set differs")
        if not np.array_equal(r.vds["Depth"].values, ref["Depth"].values):
            raise ValueError(f"{r.url}: Depth levels differ")
        for v in data_vars:
            a, b = ref[v].data.metadata, r.vds[v].data.metadata
            if (a.data_type, a.chunk_grid.chunk_shape[1:], a.codecs, a.fill_value, a.shape[1:]) != (
                b.data_type, b.chunk_grid.chunk_shape[1:], b.codecs, b.fill_value, b.shape[1:]
            ) or a.chunk_grid.chunk_shape[0] != 1:
                raise ValueError(f"{r.url}: encoding of {v} differs from {runs[0].url}")
    return data_vars


# ---------------------------------------------------------------- metadata repair
DROP_VAR_ATTRS = ("_CoordinateAxes", "field", "time", "coordinates")
# keep _FillValue: the base64 text is xarray's own Zarr-v3 encoding of 1e37, needed for masking
ATTR_REPAIRS = {
    # var: {attr: new value or None to delete}; reason in README "Metadata repairs"
    "sustr": {"units": "N m-2", "standard_name": "surface_downward_eastward_stress"},
    "svstr": {"units": "N m-2", "standard_name": "surface_downward_northward_stress"},
    "O2": {"long_name": "Dissolved oxygen concentration",
           "standard_name": "mass_concentration_of_oxygen_in_sea_water"},
    "pH": {"standard_name": None, "units": "1"},
    "omega": {"standard_name": None, "units": "1",
              "long_name": "Aragonite saturation state (omega)"},
    "Depth": {"axis": "Z"},
}


def repair_attrs(name: str, attrs: dict) -> dict:
    a = {k: v for k, v in attrs.items() if k not in DROP_VAR_ATTRS}
    for k, v in ATTR_REPAIRS.get(name, {}).items():
        if v is None:
            a.pop(k, None)
        else:
            a[k] = v
    return a


def _global_attrs(ref: xr.Dataset, layout: str, kept: list[Run], excluded) -> dict:
    g = {k: v for k, v in ref.attrs.items() if k not in ("history", "type")}
    g.update(
        title=f"TXLA ROMS forecast, z-levels ({layout})",
        source_archive=SOURCE_PREFIX,
        source_format="NetCDF-3 classic, referenced in place (virtual Icechunk)",
        layout=layout,
        source_files=[r.url.removeprefix(SOURCE_PREFIX) for r in kept],
        excluded_source_files=[f"{u.removeprefix(SOURCE_PREFIX)}: {why}" for u, why in excluded],
        metadata_repairs=(
            "Dropped per-variable _CoordinateAxes, field, time, coordinates; sustr/svstr units N s-2 -> N m-2 and CF "
            "surface_downward_*_stress names; O2 long_name 'Bottom oxygen' -> dissolved oxygen "
            "(it is 3-D) with CF mass_concentration_of_oxygen_in_sea_water; pH and omega "
            "non-CF standard_names removed, units -> 1; Depth axis=Z; dropped per-file "
            "global history and type."
        ),
    )
    return g


# ---------------------------------------------------------------- manifest assembly
def _stack(slots: list[tuple[Run, int] | None], var: str, lead_shape: tuple[int, ...]) -> ManifestArray:
    """Build a ManifestArray whose leading axes have shape `lead_shape` from one source
    record per slot (None = missing chunk -> reads as fill value)."""
    template = next(s for s in slots if s is not None)[0].vds[var].data
    rest_grid = template.manifest._paths.shape[1:]
    paths = np.full(len(slots), "", dtype=np.dtypes.StringDType())
    paths = np.broadcast_to(paths.reshape(-1, *(1,) * len(rest_grid)), (len(slots), *rest_grid)).copy()
    offsets = np.zeros(paths.shape, np.uint64)
    lengths = np.zeros(paths.shape, np.uint64)
    for i, s in enumerate(slots):
        if s is None:
            continue
        run, rec = s
        m = run.vds[var].data.manifest
        paths[i], offsets[i], lengths[i] = m._paths[rec], m._offsets[rec], m._lengths[rec]
    shp = (*lead_shape, *rest_grid)
    manifest = ChunkManifest.from_arrays(
        paths=paths.reshape(shp), offsets=offsets.reshape(shp), lengths=lengths.reshape(shp)
    )
    md = copy_and_replace_metadata(
        template.metadata,
        new_shape=[*lead_shape, *template.shape[1:]],
        new_chunks=[*(1,) * len(lead_shape), *template.metadata.chunk_grid.chunk_shape[1:]],
    )
    return ManifestArray(metadata=md, chunkmanifest=manifest)


def _coords(ref: xr.Dataset, grid: xr.Dataset) -> dict:
    c = {
        "Depth": ("Depth", ref["Depth"].values, repair_attrs("Depth", ref["Depth"].attrs)),
        "lon": (("Y", "X"), grid["lon"].values, repair_attrs("lon", grid["lon"].attrs)),
        "lat": (("Y", "X"), grid["lat"].values, repair_attrs("lat", grid["lat"].attrs)),
    }
    return c


def build_forecast(kept, excluded, grid: xr.Dataset) -> xr.Dataset:
    ref = kept[0].vds
    frt = np.array([r.frt for r in kept], dtype="datetime64[ns]")
    lead = np.arange(1, N_LEAD + 1) * np.timedelta64(1, "h")
    slots = [(r, j) if j < r.n else None for r in kept for j in range(N_LEAD)]
    data = {}
    for v in ref.data_vars:
        dims = ("forecast_reference_time", "lead", *ref[v].dims[1:])
        data[v] = (dims, _stack(slots, v, (len(kept), N_LEAD)), repair_attrs(v, ref[v].attrs))
    coords = _coords(ref, grid)
    coords.update(
        forecast_reference_time=("forecast_reference_time", frt,
                                 {"long_name": "Forecast run start (first record is lead 1 h)",
                                  "standard_name": "forecast_reference_time"}),
        lead=("lead", lead.astype("timedelta64[ns]"),
              {"long_name": "Hours after forecast_reference_time", "standard_name": "forecast_period"}),
        valid_time=(("forecast_reference_time", "lead"), frt[:, None] + lead[None, :].astype("timedelta64[ns]"),
                    {"long_name": "Valid time", "standard_name": "time"}),
    )
    return xr.Dataset(data, coords, attrs=_global_attrs(ref, "forecast", kept, excluded))


def best_slots(kept: list[Run]):
    """Map every valid hour to (run, record) from the newest run that has it."""
    owner: dict = {}
    for r in kept:  # sorted by frt, so later runs overwrite earlier ones
        for j in range(r.n):
            owner[r.frt + np.timedelta64(int(r.leads[j]), "h")] = (r, j)
    times = np.array(sorted(owner), dtype="datetime64[ns]")
    full = np.arange(times[0], times[-1] + np.timedelta64(1, "h"), np.timedelta64(1, "h"))
    return full, [owner.get(t) for t in full]


def build_best(kept, excluded, grid: xr.Dataset) -> xr.Dataset:
    ref = kept[0].vds
    times, slots = best_slots(kept)
    data = {v: (("time", *ref[v].dims[1:]), _stack(slots, v, (len(times),)), repair_attrs(v, ref[v].attrs))
            for v in ref.data_vars}
    nat = np.datetime64("NaT", "ns")
    coords = _coords(ref, grid)
    coords.update(
        time=("time", times, {"long_name": "Valid time", "standard_name": "time", "axis": "T"}),
        forecast_reference_time=("time", np.array([s[0].frt if s else nat for s in slots], "datetime64[ns]"),
                                 {"long_name": "Run that supplies this hour", "standard_name": "forecast_reference_time"}),
        lead=("time", np.array([int(s[0].leads[s[1]]) if s else -1 for s in slots]) * np.timedelta64(1, "h"),
              {"long_name": "Lead time of the supplying run", "standard_name": "forecast_period"}),
    )
    n_missing = sum(s is None for s in slots)
    attrs = _global_attrs(ref, "best", kept, excluded)
    attrs["missing_hours"] = int(n_missing)
    return xr.Dataset(data, coords, attrs=attrs)


def load_grid(kept: list[Run], registry) -> xr.Dataset:
    """lon/lat from the first and last kept runs; they must match exactly."""
    ends = [kept[0].url, kept[-1].url]
    gs = [open_virtual_dataset(u, registry=registry, parser=NetCDF3Parser(),
                               loadable_variables=["lon", "lat"])[["lon", "lat"]] for u in ends]
    for k in ("lon", "lat"):
        if not np.array_equal(gs[0][k].values, gs[1][k].values):
            raise ValueError(f"{k} differs between {ends[0]} and {ends[1]}")
    return gs[0]


# ---------------------------------------------------------------- icechunk
TIME_ENC = {"units": "seconds since 1970-01-01 00:00:00", "calendar": "gregorian", "dtype": "float64"}


def set_encodings(ds: xr.Dataset) -> None:
    """Materialized coordinates are written as ONE chunk each (performance-tuning.md).
    vz.to_icechunk takes no `encoding=` argument here, so set .encoding in place."""
    for k, v in ds.coords.items():
        e = {"chunks": v.shape}
        if np.issubdtype(v.dtype, np.datetime64):
            e |= TIME_ENC
        elif np.issubdtype(v.dtype, np.timedelta64):
            e |= {"units": "hours", "dtype": "int32"}
        ds[k].encoding = e


def repo_config(prefix: str = SOURCE_PREFIX) -> ic.RepositoryConfig:
    cfg = ic.RepositoryConfig.default()
    cfg.set_virtual_chunk_container(ic.VirtualChunkContainer(url_prefix=prefix, store=ic.http_store()))
    return cfg


def open_or_create(path: str, prefix: str = SOURCE_PREFIX) -> ic.Repository:
    storage = ic.local_filesystem_storage(path)
    auth = {prefix: ic.credentials.HttpAccess}
    if ic.Repository.exists(storage):
        repo = ic.Repository.open(storage, config=repo_config(prefix), authorize_virtual_chunk_access=auth)
    else:
        repo = ic.Repository.create(storage, config=repo_config(prefix), authorize_virtual_chunk_access=auth)
    repo.save_config()
    return repo


def write(repo: ic.Repository, forecast: xr.Dataset, best: xr.Dataset, message: str) -> str:
    """Replace both groups in one commit (the newest runs change /best every day)."""
    set_encodings(forecast)
    set_encodings(best)
    session = repo.writable_session("main")
    forecast.vz.to_icechunk(session.store, group="forecast", mode="w")
    best.vz.to_icechunk(session.store, group="best", mode="w")
    return session.commit(message)


def open_readonly(path: str, group: str, prefix: str = SOURCE_PREFIX, chunks=None) -> xr.Dataset:
    """Consumer read path: a fresh Repository.open with explicit anonymous HTTP authorization."""
    repo = ic.Repository.open(
        ic.local_filesystem_storage(path),
        authorize_virtual_chunk_access={prefix: ic.credentials.HttpAccess},
    )
    store = repo.readonly_session("main").store
    return xr.open_zarr(store, group=group, consolidated=False, chunks=chunks, zarr_format=3)
