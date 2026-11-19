"""Build a virtual Icechunk store of the global RTOFS u/v archive kept by TAMU-CC.

Source : NetCDF-4/HDF5 files on TAMU-CC's Apache server, referenced in place over HTTPS.
         One file per (run, valid day, variable): <run>.<valid>.<u|v>.nc, one time step each.
Store  : one Icechunk repository, two groups that reference the same source bytes
         /forecast  dims (forecast_reference_time, lead, Depth, Y, X)   every run, leads 1-8 days
         /best      dims (time, Depth, Y, X)   each valid day from the newest run that has it

Owner decisions (2026-11-18): shorter runs (5- and 4-day) are padded to 8 leads (missing leads
read as NaN); model versions 93.0 and 93.1 are both kept and tagged by an `experiment` coordinate;
longitude is wrapped to [-180, 180). Nothing is rewritten: u and v keep their source chunks
(1, 3, 413, 563), zlib level 1. Only coordinates and attributes are materialized or repaired.

Opening one file virtually reads its HDF5 chunk index (about 2 s over HTTPS), so the per-file
references are cached in rtofs/.ref_cache/, keyed by the Apache listing's modification time.
A refresh only opens new or rewritten files.

Used by both smoke_test.ipynb and build_rtofs_icechunk.py.
"""

from __future__ import annotations

import concurrent.futures as cf
import html
import json
import logging
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import icechunk as ic
import numpy as np
import xarray as xr
from obspec_utils.registry import ObjectStoreRegistry
from obstore.store import from_url
from virtualizarr import open_virtual_dataset
from virtualizarr.manifests import ChunkManifest, ManifestArray
from virtualizarr.manifests.utils import copy_and_replace_metadata
from virtualizarr.parsers import HDFParser

# Anonymous HTTPS authorization: icechunk 2.x has credentials.HttpAccess; 1.x authorizes with None.
HTTP_ACCESS = getattr(ic.credentials, "HttpAccess", None)

log = logging.getLogger("rtofs")

SOURCE_PREFIX = "https://hriai01.tamucc.edu/data/MASTR_MODELS/rtofs/history/"  # must end in "/"
NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{2})\.(\d{4}-\d{2}-\d{2}-\d{2})\.(u|v)\.nc$")
VARS = ("u", "v")
N_LEAD = 8  # lead days of a full run
SHAPE = (33, 3298, 4500)  # Depth, Y, X
CHUNK = (3, 413, 563)
CACHE_VERSION = 1
HERE = Path(__file__).parent
CACHE_DIR = HERE / ".ref_cache"

# ---------------------------------------------------------------- discovery
_DIR = re.compile(r'<a href="(\d{4}/|\d{2}/)">')
_ROW = re.compile(
    r'<a href="([^"?/][^"]*)">[^<]*</a></td><td align="right">([\d-]+ [\d:]+)\s*</td>'
    r'<td align="right">\s*([^<]*?)\s*</td>'
)


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


@dataclass(frozen=True)
class SourceFile:
    path: str  # relative to SOURCE_PREFIX, e.g. 2026/11/2026-11-16-00.2026-11-17-00.u.nc
    listed_mtime: str  # Apache listing "Last modified" (server local time, minute resolution)
    listed_size: str
    run: np.datetime64
    valid: np.datetime64
    var: str

    @property
    def url(self) -> str:
        return SOURCE_PREFIX + self.path

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[1]

    @property
    def lead(self) -> int:
        return int((self.valid - self.run) // np.timedelta64(1, "D"))


def discover(prefix: str = SOURCE_PREFIX) -> tuple[list[SourceFile], list[str]]:
    """Walk the YYYY/MM/ folders of the Apache listing.

    Returns (matching files sorted by name, other entries that did not match the pattern)."""
    files, other = [], []
    for y in _DIR.findall(_get(prefix)):
        for m in _DIR.findall(_get(prefix + y)):
            page = _get(prefix + y + m)
            names = set(re.findall(r'<a href="([^"?/][^"]*)">', page))
            rows = {html.unescape(n): (mt, sz) for n, mt, sz in _ROW.findall(page)}
            for n in sorted(names):
                n = html.unescape(n)
                mo = NAME_RE.match(n)
                if not mo or n not in rows:
                    other.append(y + m + n)
                    continue
                run, valid = (np.datetime64(f"{s[:10]}T{s[11:13]}", "ns") for s in mo.group(1, 2))
                files.append(SourceFile(y + m + n, *rows[n], run, valid, mo.group(3)))
    files.sort(key=lambda f: f.name)
    return files, other


# ---------------------------------------------------------------- per-file references (cached)
@dataclass
class FileRef:
    src: SourceFile
    offsets: np.ndarray  # (11, 8, 8) uint64 chunk offsets of the u or v array
    lengths: np.ndarray  # (11, 8, 8) uint64 chunk lengths
    experiment: str
    signature: str  # encoding of the science array; must match across all files
    grid_signature: str  # compressed chunk lengths of Latitude/Longitude/Depth values


def make_registry(prefix: str = SOURCE_PREFIX) -> ObjectStoreRegistry:
    return ObjectStoreRegistry({prefix: from_url(prefix)})


def _signature(ma: ManifestArray) -> str:
    md = ma.metadata
    return repr((str(md.data_type), tuple(md.shape), tuple(md.chunk_grid.chunk_shape),
                 [c.to_dict() for c in md.codecs], float(md.fill_value)))


def open_virtual(url: str, registry: ObjectStoreRegistry, load=("MT", "Depth")) -> xr.Dataset:
    return open_virtual_dataset(url, registry=registry, parser=HDFParser(), loadable_variables=list(load))


def _cache_path(src: SourceFile) -> Path:
    return CACHE_DIR / (src.name + ".npz")


@dataclass
class Unreadable:
    """A source file whose HDF5 structure cannot be parsed (h5py and netCDF-C both fail)."""
    src: SourceFile
    reason: str


# Errors that mean the file itself is broken, not that the connection dropped. Seen 2026-11-18:
# 'incorrect metadata checksum' on the attribute block of v in 2025-07-29-00.2025-08-01-00.v.nc,
# which netCDF-C also refuses to open.
STRUCTURAL_ERRORS = ("checksum", "file signature not found", "truncated file", "bad object header")


def _from_cache(src: SourceFile) -> FileRef | Unreadable | None:
    p = _cache_path(src)
    if not p.exists():
        return None
    z = np.load(p)
    meta = json.loads(str(z["meta"]))
    if meta.get("cache_version") != CACHE_VERSION or meta["listed_mtime"] != src.listed_mtime:
        return None  # file was rewritten (or the cache format changed): re-open it
    if "unreadable" in meta:
        return Unreadable(src, meta["unreadable"])
    return FileRef(src, z["offsets"], z["lengths"], meta["experiment"], meta["signature"], meta["grid_signature"])


def _write_cache(src: SourceFile, meta: dict, **arrays) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    meta = dict(cache_version=CACHE_VERSION, listed_mtime=src.listed_mtime, **meta)
    tmp = _cache_path(src).with_suffix(".tmp.npz")
    np.savez(tmp, meta=json.dumps(meta), **arrays)
    tmp.replace(_cache_path(src))


def open_file(src: SourceFile, registry: ObjectStoreRegistry, use_cache: bool = True) -> FileRef:
    """Virtual open of one file (HDF5 chunk index + MT + Depth only) with consistency checks."""
    if use_cache and isinstance(r := _from_cache(src), FileRef):
        return r
    vds = open_virtual(src.url, registry)
    if src.var not in vds.data_vars or set(vds.data_vars) - {src.var}:
        raise ValueError(f"{src.path}: expected one data variable {src.var!r}, found {list(vds.data_vars)}")
    mt = vds["MT"].values.astype("datetime64[ns]")
    if mt.shape != (1,) or mt[0] != src.valid:
        raise ValueError(f"{src.path}: MT {mt} does not match the valid time in the filename")
    ma = vds[src.var].data
    if tuple(ma.shape) != (1, *SHAPE) or tuple(ma.metadata.chunk_grid.chunk_shape) != (1, *CHUNK):
        raise ValueError(f"{src.path}: shape {ma.shape} / chunks {ma.metadata.chunk_grid.chunk_shape} unexpected")
    paths = np.unique(ma.manifest._paths)
    if list(paths) != [src.url]:
        raise ValueError(f"{src.path}: manifest points at {paths}, not the file itself")
    grid_sig = repr([vds[k].data.manifest._lengths.ravel().tolist() for k in ("Latitude", "Longitude")]
                    + [vds["Depth"].values.tolist()])
    ref = FileRef(src, ma.manifest._offsets[0].copy(), ma.manifest._lengths[0].copy(),
                  str(vds.attrs.get("experiment", "")), _signature(ma), grid_sig)
    if use_cache:
        _write_cache(src, dict(experiment=ref.experiment, signature=ref.signature,
                               grid_signature=ref.grid_signature), offsets=ref.offsets, lengths=ref.lengths)
    return ref


_WORKER_REGISTRY = None


def _open_in_worker(args) -> FileRef | Unreadable:
    """Process-pool entry point; parallelism uses processes, one registry each.
    Structural HDF5 errors (STRUCTURAL_ERRORS) are returned, and cached, as Unreadable after three
    attempts; anything else (network, failed consistency check) is raised and stops the build."""
    global _WORKER_REGISTRY
    src, use_cache = args
    if _WORKER_REGISTRY is None:
        _WORKER_REGISTRY = make_registry()
    for attempt in range(3):
        try:
            return open_file(src, _WORKER_REGISTRY, use_cache)
        except (OSError, RuntimeError) as e:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            msg = str(e)
            if any(k in msg.lower() for k in STRUCTURAL_ERRORS):
                if use_cache:
                    _write_cache(src, dict(unreadable=msg))
                return Unreadable(src, msg)
            raise RuntimeError(f"{src.path}: {e}") from e
    raise AssertionError


def open_files(files, workers: int = 4, use_cache: bool = True, retry_unreadable: bool = False,
               progress_every: int = 100) -> tuple[list[FileRef], list[Unreadable]]:
    """Open many files. A few workers only: this is a university web server others use too.
    Returns (readable refs, unreadable files), both in the order of `files`."""
    t0 = time.time()
    done: dict = {}
    for f in files:
        c = _from_cache(f) if use_cache else None
        if c is not None and not (retry_unreadable and isinstance(c, Unreadable)):
            done[f.path] = c
    todo = [f for f in files if f.path not in done]
    log.info("%d files: %d cached, %d to open", len(files), len(done), len(todo))
    if todo:
        import multiprocessing as mp
        with cf.ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn")) as ex:
            for i, r in enumerate(ex.map(_open_in_worker, [(f, use_cache) for f in todo]), 1):
                done[r.src.path] = r
                if isinstance(r, Unreadable):
                    log.warning("UNREADABLE %s: %s", r.src.path, r.reason)
                if i % progress_every == 0 or i == len(todo):
                    log.info("opened %d/%d new files (%.0fs)", i, len(todo), time.time() - t0)
    res = [done[f.path] for f in files]
    return [r for r in res if isinstance(r, FileRef)], [r for r in res if isinstance(r, Unreadable)]


# ---------------------------------------------------------------- consistency checks
def check_listing(files: list[SourceFile]) -> None:
    """Before opening anything: one u and one v per (run, valid), no duplicates, leads 1..8."""
    keys: dict = {}
    for f in files:
        d = keys.setdefault((f.run, f.valid), {})
        if f.var in d:
            raise ValueError(f"duplicate file for {(f.run, f.valid)} {f.var}: {f.path}")
        d[f.var] = f
    unpaired = [k for k, d in keys.items() if set(d) != set(VARS)]
    if unpaired:
        raise ValueError(f"{len(unpaired)} (run, valid) pairs lack u or v in the listing, e.g. {unpaired[:3]}")
    bad_lead = [f.path for f in files if not 1 <= f.lead <= N_LEAD]
    if bad_lead:
        raise ValueError(f"lead outside 1..{N_LEAD} days: {bad_lead[:5]}")


def check_compatible(refs: list[FileRef]) -> None:
    """Every readable file must share the science-array encoding, the grid, and its run's experiment."""
    sig = {r.signature for r in refs}
    if len(sig) != 1:
        raise ValueError(f"science-array encoding differs between files: {sig}")
    gsig = {r.grid_signature for r in refs}
    if len(gsig) != 1:
        bad = [r.src.path for r in refs if r.grid_signature != refs[0].grid_signature][:5]
        raise ValueError(f"grid (Latitude/Longitude/Depth) differs from {refs[0].src.path}, e.g. {bad}")
    per_run_exp = {}
    for r in refs:
        per_run_exp.setdefault(r.src.run, set()).add(r.experiment)
    mixed = {k: v for k, v in per_run_exp.items() if len(v) > 1}
    if mixed:
        raise ValueError(f"runs with mixed experiment attributes: {mixed}")


# ---------------------------------------------------------------- templates and grid
@dataclass
class Templates:
    arrays: dict  # var -> ManifestArray from a live open (for its Zarr metadata)
    attrs: dict  # var -> source attributes
    depth: xr.DataArray
    lat: np.ndarray
    lon: np.ndarray  # as in the source (74..794)
    lat_attrs: dict
    lon_attrs: dict
    global_attrs: dict


def load_templates(refs: list[FileRef], registry) -> Templates:
    """Open the newest u and v files live (their Zarr metadata and attributes) and read the grid
    once. The grid is cached; check_compatible() proves every file has the same compressed grid."""
    newest = {v: max((r for r in refs if r.src.var == v), key=lambda r: r.src.name) for v in VARS}
    arrays, attrs = {}, {}
    for v, r in newest.items():
        vds = open_virtual(r.src.url, registry)
        arrays[v], attrs[v] = vds[v].data, dict(vds[v].attrs)
        if _signature(arrays[v]) != r.signature:
            raise ValueError(f"{r.src.path}: live encoding differs from the cached one")
    grid_file = CACHE_DIR / "grid.npz"
    ref = newest["u"]
    if grid_file.exists() and str(np.load(grid_file)["grid_signature"]) == ref.grid_signature:
        g = np.load(grid_file)
        lat, lon = g["lat"], g["lon"]
    else:
        log.info("reading Latitude/Longitude from %s", ref.src.path)
        gds = open_virtual(ref.src.url, registry, load=("MT", "Depth", "Latitude", "Longitude"))
        lat, lon = gds["Latitude"].values, gds["Longitude"].values
        CACHE_DIR.mkdir(exist_ok=True)
        np.savez(grid_file, lat=lat, lon=lon, grid_signature=ref.grid_signature)
    vds = open_virtual(ref.src.url, registry)
    return Templates(arrays, attrs, vds["Depth"], lat, lon, dict(vds["Latitude"].attrs),
                     dict(vds["Longitude"].attrs), dict(vds.attrs))


# ---------------------------------------------------------------- metadata repair
VAR_NAMES = {"u": "Eastward sea water velocity", "v": "Northward sea water velocity"}


def repair_var_attrs(var: str, attrs: dict) -> dict:
    """u/v: drop per-file valid_range, the source `coordinates` (names Date, which is dropped),
    version-tagged long_name; CF units spelling."""
    a = {k: v for k, v in attrs.items() if k not in ("valid_range", "coordinates", "long_name")}
    a["long_name"] = VAR_NAMES[var]
    a["units"] = "m s-1"
    return a


def wrap_lon(lon: np.ndarray) -> np.ndarray:
    return (((lon.astype(np.float64) + 180.0) % 360.0) - 180.0).astype(np.float32)


METADATA_REPAIRS = (
    "Longitude wrapped from the source's 74..794 (modulo 360) to [-180, 180), grid order unchanged; "
    "lat/lon stored once instead of in every file. u/v: per-file valid_range dropped (it differs "
    "between files), long_name without the model version tag (version is the `experiment` "
    "coordinate), units m/s -> m s-1, `coordinates` attribute dropped. Dropped the Date variable "
    "(day as %Y%m%d.%f, redundant with time) and the X/Y index variables (1..N). Source time "
    "(MT, days since 1900-12-31) replaced by forecast_reference_time/lead/valid_time decoded from "
    "it and from the filename (checked equal for every file). Conventions CF-1.0 -> CF-1.8."
)


def _global_attrs(t: Templates, layout: str, refs: list[FileRef]) -> dict:
    exps = sorted({r.experiment for r in refs})
    return dict(
        Conventions="CF-1.8",
        title=f"Global RTOFS (HYCOM 1/12 deg) ocean velocity, z-levels ({layout})",
        institution=t.global_attrs.get("institution", ""),
        source=f"{t.global_attrs.get('title', '')} ({t.global_attrs.get('source', '')}), "
               f"experiments {', '.join(exps)}; files converted by {t.global_attrs.get('history', '')}",
        source_archive=SOURCE_PREFIX,
        source_format="NetCDF-4/HDF5, zlib level 1, referenced in place (virtual Icechunk)",
        layout=layout,
        source_file_count=len(refs),
        metadata_repairs=METADATA_REPAIRS,
    )


def _coords(t: Templates) -> dict:
    lat_a = {k: v for k, v in t.lat_attrs.items() if k not in ("_FillValue",)}
    lon_a = {k: v for k, v in t.lon_attrs.items() if k not in ("_FillValue", "modulo")}
    da = dict(t.depth.attrs)
    da.setdefault("positive", "down")
    da.setdefault("axis", "Z")
    return {
        "Depth": ("Depth", t.depth.values, da),
        "Latitude": (("Y", "X"), t.lat, lat_a),
        "Longitude": (("Y", "X"), wrap_lon(t.lon), lon_a),
    }


# ---------------------------------------------------------------- manifest assembly
def _stack(slots: list[FileRef | None], t: Templates, var: str, lead_shape: tuple[int, ...]) -> ManifestArray:
    """ManifestArray with leading axes `lead_shape`, one source file per slot (None -> no chunk,
    reads as the fill value)."""
    grid = (SHAPE[0] // CHUNK[0], -(-SHAPE[1] // CHUNK[1]), -(-SHAPE[2] // CHUNK[2]))  # (11, 8, 8)
    n = len(slots)
    paths = np.full((n, *grid), "", dtype=np.dtypes.StringDType())
    offsets = np.zeros((n, *grid), np.uint64)
    lengths = np.zeros((n, *grid), np.uint64)
    for i, r in enumerate(slots):
        if r is None:
            continue
        paths[i] = r.src.url
        offsets[i], lengths[i] = r.offsets, r.lengths
    shp = (*lead_shape, *grid)
    manifest = ChunkManifest.from_arrays(paths=paths.reshape(shp), offsets=offsets.reshape(shp),
                                         lengths=lengths.reshape(shp))
    md = copy_and_replace_metadata(t.arrays[var].metadata, new_shape=[*lead_shape, *SHAPE],
                                   new_chunks=[*(1,) * len(lead_shape), *CHUNK])
    return ManifestArray(metadata=md, chunkmanifest=manifest)


def _index(refs):
    by = {}
    for r in refs:
        by[(r.src.run, r.src.lead, r.src.var)] = r
    return by


def build_forecast(refs: list[FileRef], t: Templates, variables=VARS) -> xr.Dataset:
    """`variables`: which science arrays to assemble (() gives coordinates only). Each array holds
    ~1.5M chunk references (~0.6 GB in memory), so write() assembles them one at a time."""
    by = _index(refs)
    runs = np.array(sorted({r.src.run for r in refs}), dtype="datetime64[ns]")
    exp = {r.src.run: r.experiment for r in refs}
    lead = (np.arange(1, N_LEAD + 1) * np.timedelta64(1, "D")).astype("timedelta64[ns]")
    dims = ("forecast_reference_time", "lead", "Depth", "Y", "X")
    data = {}
    for v in variables:
        slots = [by.get((run, j, v)) for run in runs for j in range(1, N_LEAD + 1)]
        data[v] = (dims, _stack(slots, t, v, (len(runs), N_LEAD)), repair_var_attrs(v, t.attrs[v]))
    n_leads = np.array([sum(((run, j, "u") in by or (run, j, "v") in by) for j in range(1, N_LEAD + 1))
                        for run in runs], np.int8)
    coords = _coords(t)
    coords.update(
        forecast_reference_time=("forecast_reference_time", runs,
                                 {"long_name": "Forecast run start (00 UTC)", "standard_name": "forecast_reference_time"}),
        lead=("lead", lead, {"long_name": "Days after forecast_reference_time", "standard_name": "forecast_period"}),
        valid_time=(("forecast_reference_time", "lead"), runs[:, None] + lead[None, :],
                    {"long_name": "Valid time", "standard_name": "time"}),
        experiment=("forecast_reference_time", np.array([exp[r] for r in runs], dtype="<U8"),
                    {"long_name": "RTOFS/HYCOM experiment (model version) of the run"}),
        n_leads=("forecast_reference_time", n_leads,
                 {"long_name": "Number of lead days the source archive holds for this run",
                  "comment": "Leads beyond this read as missing (NaN)"}),
    )
    attrs = _global_attrs(t, "forecast", refs)
    return xr.Dataset(data, coords, attrs=attrs)


def best_slots(refs: list[FileRef]):
    """Every valid day from first to last, from the newest run with readable u AND v for it
    (None = no such run)."""
    have = {(r.src.run, r.src.lead, r.src.var) for r in refs}
    owner: dict = {}
    for r in sorted((r for r in refs if r.src.var == "u"), key=lambda r: r.src.run):
        if (r.src.run, r.src.lead, "v") in have:  # both variables come from the same run
            owner[r.src.valid] = (r.src.run, r.src.lead)  # later runs overwrite earlier ones
    days = np.arange(min(owner), max(owner) + np.timedelta64(1, "D"), np.timedelta64(1, "D")).astype("datetime64[ns]")
    return days, [owner.get(d) for d in days]


def build_best(refs: list[FileRef], t: Templates, variables=VARS) -> xr.Dataset:
    by = _index(refs)
    exp = {r.src.run: r.experiment for r in refs}
    days, owners = best_slots(refs)
    dims = ("time", "Depth", "Y", "X")
    data = {v: (dims, _stack([by[(o[0], o[1], v)] if o else None for o in owners], t, v, (len(days),)),
                repair_var_attrs(v, t.attrs[v])) for v in variables}
    nat = np.datetime64("NaT", "ns")
    coords = _coords(t)
    coords.update(
        time=("time", days, {"long_name": "Valid time", "standard_name": "time", "axis": "T"}),
        forecast_reference_time=("time", np.array([o[0] if o else nat for o in owners], "datetime64[ns]"),
                                 {"long_name": "Run that supplies this day", "standard_name": "forecast_reference_time"}),
        lead=("time", (np.array([o[1] if o else -1 for o in owners]) * np.timedelta64(1, "D")).astype("timedelta64[ns]"),
              {"long_name": "Lead of the supplying run (-1 day: no run covers this day)",
               "standard_name": "forecast_period"}),
        experiment=("time", np.array([exp[o[0]] if o else "" for o in owners], dtype="<U8"),
                    {"long_name": "RTOFS/HYCOM experiment of the supplying run"}),
    )
    attrs = _global_attrs(t, "best", refs)
    attrs["missing_days"] = int(sum(o is None for o in owners))
    return xr.Dataset(data, coords, attrs=attrs)


# ---------------------------------------------------------------- icechunk
TIME_ENC = {"units": "seconds since 1970-01-01 00:00:00", "calendar": "proleptic_gregorian", "dtype": "float64"}


def set_encodings(ds: xr.Dataset) -> None:
    """Materialized coordinates: one chunk each for 1-D/2-D small ones (performance-tuning.md);
    the 59 MB lat/lon in 4 chunks. vz.to_icechunk takes no `encoding=`, so set .encoding in place."""
    for k, v in ds.coords.items():
        e = {"chunks": v.shape}
        if k in ("Latitude", "Longitude"):
            e = {"chunks": (-(-v.shape[0] // 2), -(-v.shape[1] // 2))}
        if np.issubdtype(v.dtype, np.datetime64):
            e |= TIME_ENC
        elif np.issubdtype(v.dtype, np.timedelta64):
            e |= {"units": "days", "dtype": "int32"}
        ds[k].encoding = e


def repo_config(prefix: str = SOURCE_PREFIX) -> ic.RepositoryConfig:
    cfg = ic.RepositoryConfig.default()
    cfg.set_virtual_chunk_container(ic.VirtualChunkContainer(url_prefix=prefix, store=ic.http_store()))
    # ~2.8M chunk references in /forecast: split manifests along the run/time axes so a reader of
    # recent data does not fetch the whole history's references (performance-tuning.md; starting point)
    cfg.manifest = ic.ManifestConfig(
        splitting=ic.ManifestSplittingConfig.from_dict(
            {ic.ManifestSplitCondition.AnyArray(): {
                ic.ManifestSplitDimCondition.DimensionName("forecast_reference_time"): 30,
                ic.ManifestSplitDimCondition.DimensionName("time"): 120,
            }}
        )
    )
    return cfg


def open_or_create(path: str, prefix: str = SOURCE_PREFIX) -> ic.Repository:
    storage = ic.local_filesystem_storage(path)
    auth = {prefix: HTTP_ACCESS}
    if ic.Repository.exists(storage):
        repo = ic.Repository.open(storage, config=repo_config(prefix), authorize_virtual_chunk_access=auth)
    else:
        repo = ic.Repository.create(storage, config=repo_config(prefix), authorize_virtual_chunk_access=auth)
    repo.save_config()
    return repo


GRID_VARS = ("Latitude", "Longitude")


def grid_sha256(t: Templates) -> str:
    import hashlib
    return hashlib.sha256(t.lat.tobytes() + wrap_lon(t.lon).tobytes()).hexdigest()


def _clear_group_keep_grid(store, group: str, sha: str) -> bool:
    """If `group` exists with the same grid, delete every array except Latitude/Longitude and return
    True (the caller then writes with mode="a"). Otherwise leave it alone and return False.
    Deleting first is needed because to_zarr(mode="a") refuses to resize existing arrays."""
    import zarr
    root = zarr.open_group(store, mode="a")
    try:
        g = root[group]
    except KeyError:
        return False
    names = set(g.array_keys())
    if g.attrs.get("grid_sha256") != sha or not set(GRID_VARS) <= names:
        return False
    for k in names - set(GRID_VARS):
        del g[k]
    return True


def _to_icechunk(ds: xr.Dataset, store, group: str, mode: str) -> None:
    """ds.vz.to_icechunk(store, group=group, mode=mode) on any VirtualiZarr 2.x.

    VirtualiZarr < 2.5 (the newest on Python 3.11, which icechunk 1.x needs) has no `mode=`: it
    always creates the group and fails if it exists. Emulate "w" by deleting the group first, and
    "a" by writing into the existing group with the same lower-level writer to_icechunk uses
    (coordinates via to_zarr(mode="a"), virtual arrays via require_array)."""
    import inspect

    import zarr

    if "mode" in inspect.signature(ds.vz.to_icechunk).parameters:
        ds.vz.to_icechunk(store, group=group, mode=mode)
        return
    from virtualizarr.writers.icechunk import (validate_virtual_chunk_containers,
                                               write_virtual_dataset_to_icechunk_group)
    from zarr.storage import StorePath

    root = zarr.open_group(store, mode="a")
    exists = group in root
    if mode == "w" and exists:
        del root[group]
        exists = False
    if not exists:
        ds.vz.to_icechunk(store, group=group)
        return
    validate_virtual_chunk_containers(store.session.config, [ds])
    g = zarr.Group.open(StorePath(store, path=group), zarr_format=3)
    write_virtual_dataset_to_icechunk_group(vds=ds, store=store, group=g)


def write(repo: ic.Repository, refs: list[FileRef], t: Templates, message: str) -> str:
    """Replace both groups, one (group, variable) per commit on a temporary build branch, then move
    `main` to the final snapshot in one step, so readers of main never see a half-written store.

    One session holding all four arrays (~3M chunk references) exceeded this machine's 3.9 GB
    memory limit (2026-11-18); one array at a time peaks at about 1.7 GB."""
    import gc

    import zarr

    sha = grid_sha256(t)
    base = repo.lookup_branch("main")
    branch = f"build-{time.strftime('%Y%m%dT%H%M%S')}"
    repo.create_branch(branch, snapshot_id=base)
    snap = base
    for group, builder in (("forecast", build_forecast), ("best", build_best)):
        for i, v in enumerate(VARS):
            ds = builder(refs, t, variables=(v,))
            ds.attrs["grid_sha256"] = sha
            session = repo.writable_session(branch)
            if i == 0:
                keep_grid = _clear_group_keep_grid(session.store, group, sha)
            if i == 0 and not keep_grid:
                mode = "w"  # first build, or the grid changed: rewrite the whole group
            else:
                # Latitude/Longitude (~67 MB of materialized chunks) are already stored and unchanged;
                # rewriting them in every commit grew the repository ~160 MB per refresh.
                mode = "a"
                ds = ds.drop_vars(GRID_VARS)
            set_encodings(ds)
            _to_icechunk(ds, session.store, group, mode)
            if mode == "a":  # the writer lists only the coords it wrote; keep lat/lon as coordinates
                g = zarr.open_group(session.store, mode="a", path=group)
                coords = (g.attrs.get("coordinates") or "").split()
                g.attrs["coordinates"] = " ".join(coords + [c for c in GRID_VARS if c not in coords])
            snap = session.commit(f"{message} [{group}/{v}]")
            log.info("committed %s/%s (%s): %s", group, v, "grid kept" if mode == "a" else "group rewritten", snap)
            del ds, session
            gc.collect()
    repo.reset_branch("main", snap, from_snapshot_id=base)  # fails if main moved meanwhile
    repo.delete_branch(branch)  # temporary Icechunk branch inside the store (not a git branch)
    return snap


def open_readonly(path: str, group: str, prefix: str = SOURCE_PREFIX, chunks=None) -> xr.Dataset:
    """Consumer read path: a fresh Repository.open with explicit anonymous HTTP authorization."""
    repo = ic.Repository.open(
        ic.local_filesystem_storage(path),
        authorize_virtual_chunk_access={prefix: HTTP_ACCESS},
    )
    store = repo.readonly_session("main").store
    return xr.open_zarr(store, group=group, consolidated=False, chunks=chunks, zarr_format=3)
