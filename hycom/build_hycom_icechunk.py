"""Build the HYCOM 2.2.99+CICE Currents virtual Icechunk store (full archive).

    python build_hycom_icechunk.py [--repo ../hycom_icechunks] [--runs N] [--workers 6] [--scan-only]

Source : https://hriai01.tamucc.edu/data/MASTR_MODELS/hycom/history/  (TAMU-CC Apache server)
         NetCDF-3 classic, one valid time per file, water_u and water_v each one contiguous,
         uncompressed big-endian int16 block of 40 x 4251 x 4500 (1.53 GB). See source-audit.md.
Store  : /forecast  (forecast_reference_time, lead, depth, lat, lon)   every 12 UTC run, leads 0-192 h / 3 h
         /best      (time, depth, lat, lon)   3-hourly, each step from the newest run that has it

Sub-chunking: the source blocks are uncompressed, so each is described as 1,040 byte ranges of
1 depth x 327 lat rows x 4500 lon (2.94 MB) instead of one 1.53 GB chunk. 327 divides 4251, so no
chunk runs past its block. Nothing is copied; only coordinates are materialized.

Each run: list the archive, read each new file's first 80 kB (header, coordinates, time, tau; one
range request, cached in .scan_cache.jsonl by listing mtime), create the arrays at full size, set the
virtual references in batches (one commit per batch, on a temporary branch), move `main`, validate
against the source with netCDF4, and tag `data-through-<last valid day>`.
Every reference carries the file's ETag: if TAMU-CC rewrites a file, reads of it fail instead of
silently returning the new bytes; rerun the build to pick up the new version.

--runs N keeps the first and last N runs (smoke test; use a scratch --repo).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import html
import json
import logging
import re
import struct
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import icechunk as ic
import numpy as np
import xarray as xr
import zarr

log = logging.getLogger("hycom")
HERE = Path(__file__).parent
PREFIX = "https://hriai01.tamucc.edu/data/MASTR_MODELS/hycom/history/"  # must end in "/"
NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}-\d{2})\.(\d{4}-\d{2}-\d{2}-\d{2})\.nc$")
VARS = ("water_u", "water_v")
NZ, NY, NX = 40, 4251, 4500
CY = 327  # lat rows per chunk; 4251 = 13 x 327
LEADS_H = np.arange(0, 193, 3)  # 65 leads
HEAD_BYTES = 81920
CACHE = HERE / ".scan_cache.jsonl"
SCAN_VERSION = 1
# Anonymous HTTPS authorization: icechunk 2.x has credentials.HttpAccess; 1.x authorizes with None.
HTTP_ACCESS = getattr(ic.credentials, "HttpAccess", None)


# ------------------------------------------------------------------ discovery
_DIR = re.compile(r'<a href="(\d{4}/|\d{2}/)">')
_ROW = re.compile(r'<a href="([^"?/][^"]*)">[^<]*</a></td><td align="right">([\d-]+ [\d:]+)\s*</td>'
                  r'<td align="right">\s*([^<]*?)\s*</td>')


def _get(url: str, headers=None, tries: int = 5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read(), dict(r.headers)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(3 * (i + 1))
    raise AssertionError


@dataclass(frozen=True)
class SourceFile:
    path: str  # relative to PREFIX
    mtime: str  # listing "Last modified" (server local time)
    run: np.datetime64
    valid: np.datetime64

    @property
    def url(self) -> str:
        return PREFIX + self.path

    @property
    def lead_h(self) -> int:
        return int((self.valid - self.run) // np.timedelta64(1, "h"))


def discover() -> list[SourceFile]:
    out = []
    for y in _DIR.findall(_get(PREFIX)[0].decode()):
        for m in _DIR.findall(_get(PREFIX + y)[0].decode()):
            page = _get(PREFIX + y + m)[0].decode()
            time.sleep(0.2)
            for n, mt, _ in _ROW.findall(page):
                n = html.unescape(n)
                mo = NAME_RE.match(n)
                if not mo:
                    log.warning("not matching the file pattern, ignored: %s", y + m + n)
                    continue
                run, valid = (np.datetime64(f"{s[:10]}T{s[11:13]}", "ns") for s in mo.group(1, 2))
                out.append(SourceFile(y + m + n, mt, run, valid))
    out.sort(key=lambda f: (f.run, f.valid))
    return out


# ------------------------------------------------------------------ NetCDF-3 header scan
_T = {1: ("b", 1), 2: ("c", 1), 3: ("h", 2), 4: ("i", 4), 5: ("f", 4), 6: ("d", 8)}


def parse_cdf3(buf: bytes) -> dict:
    """Minimal NetCDF-3 classic (CDF-1/CDF-2) header parser: dims, global attrs, vars with begin."""
    if buf[:3] != b"CDF" or buf[3] not in (1, 2):
        raise ValueError(f"not NetCDF-3 classic: {buf[:4]!r}")
    pos = 4

    def u32():
        nonlocal pos
        pos += 4
        return struct.unpack(">I", buf[pos - 4:pos])[0]

    def name():
        nonlocal pos
        n = u32()
        s = buf[pos:pos + n].decode()
        pos += (n + 3) // 4 * 4
        return s

    def attrs():
        nonlocal pos
        u32()
        out = {}
        for _ in range(u32()):
            k, t, m = name(), u32(), u32()
            f, sz = _T[t]
            raw = buf[pos:pos + m * sz]
            pos += (m * sz + 3) // 4 * 4
            out[k] = raw.decode(errors="replace") if t == 2 else list(struct.unpack(">" + f * m, raw))
        return out

    ver = buf[3]
    numrecs = u32()
    u32()
    dims = [(name(), u32()) for _ in range(u32())]
    gattrs = attrs()
    u32()
    variables = {}
    for _ in range(u32()):
        n = name()
        ids = [u32() for _ in range(u32())]
        a = attrs()
        t = u32()
        vsize = u32()
        if ver == 2:
            begin = struct.unpack(">Q", buf[pos:pos + 8])[0]
            pos += 8
        else:
            begin = u32()
        variables[n] = dict(dims=[dims[i][0] for i in ids], type=_T[t][0], vsize=vsize, begin=begin, attrs=a)
    return dict(numrecs=numrecs, dims=dict(dims), gattrs=gattrs, vars=variables)


def _arr(buf, v, dtype=">f8"):
    return np.frombuffer(buf[v["begin"]:v["begin"] + v["vsize"]], dtype)


def scan_file(f: SourceFile) -> dict:
    """One range request: header + depth/lat/lon/time/tau. Checks every assumption the store relies on."""
    buf, hdr = _get(f.url, {"Range": f"bytes=0-{HEAD_BYTES - 1}"})
    total = int(hdr["Content-Range"].rsplit("/", 1)[1])
    h = parse_cdf3(buf)
    V = h["vars"]
    if h["numrecs"] != 1 or h["dims"] != {"time": 0, "depth": NZ, "lat": NY, "lon": NX}:
        raise ValueError(f"{f.path}: numrecs {h['numrecs']} dims {h['dims']}")
    for v in VARS:
        a = V[v]
        if (a["dims"] != ["time", "depth", "lat", "lon"] or a["type"] != "h" or a["vsize"] != NZ * NY * NX * 2
                or a["attrs"].get("_FillValue") != [-30000] or abs(a["attrs"]["scale_factor"][0] - 0.001) > 1e-9
                or a["attrs"].get("add_offset") != [0.0]):
            raise ValueError(f"{f.path}: unexpected encoding of {v}: {a}")
        if a["begin"] + a["vsize"] > total:
            raise ValueError(f"{f.path}: {v} runs past the end of the file")
    for k in ("depth", "lat", "lon", "time", "tau"):
        if V[k]["begin"] + V[k]["vsize"] > HEAD_BYTES:
            raise ValueError(f"{f.path}: {k} beyond the first {HEAD_BYTES} bytes")
    t = float(_arr(buf, V["time"])[0])
    tau = float(_arr(buf, V["tau"])[0])
    valid = np.datetime64("2000-01-01T00", "ns") + np.timedelta64(int(round(t * 3600)), "s")
    if valid != f.valid or int(round(tau)) != f.lead_h:
        raise ValueError(f"{f.path}: time {valid} / tau {tau} disagree with the filename")
    grid = hashlib.sha256(b"".join(_arr(buf, V[k]).tobytes() for k in ("depth", "lat", "lon"))).hexdigest()[:16]
    return dict(scan_version=SCAN_VERSION, path=f.path, mtime=f.mtime, etag=hdr.get("ETag"), size=total,
                begin_u=V["water_u"]["begin"], begin_v=V["water_v"]["begin"], grid=grid,
                history=h["gattrs"].get("history", "")[:200])


def scan(files: list[SourceFile], workers: int) -> dict[str, dict]:
    cache = {}
    if CACHE.exists():
        for line in CACHE.read_text().splitlines():
            r = json.loads(line)
            if r.get("scan_version") == SCAN_VERSION:
                cache[(r["path"], r["mtime"])] = r
    todo = [f for f in files if (f.path, f.mtime) not in cache]
    log.info("%d files: %d scanned before, %d to scan", len(files), len(files) - len(todo), len(todo))
    t0 = time.time()
    with cf.ThreadPoolExecutor(workers) as ex, open(CACHE, "a") as out:
        for i, r in enumerate(ex.map(scan_file, todo), 1):
            cache[(r["path"], r["mtime"])] = r
            out.write(json.dumps(r) + "\n")
            if i % 500 == 0 or i == len(todo):
                out.flush()
                log.info("scanned %d/%d (%.0fs)", i, len(todo), time.time() - t0)
    return {f.path: cache[(f.path, f.mtime)] for f in files}


def read_grid(f: SourceFile) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    buf, _ = _get(f.url, {"Range": f"bytes=0-{HEAD_BYTES - 1}"})
    h = parse_cdf3(buf)
    V = h["vars"]
    return _arr(buf, V["depth"]), _arr(buf, V["lat"]), _arr(buf, V["lon"]), h


# ------------------------------------------------------------------ store layout
def _clean(a: dict) -> dict:
    out = {}
    for k, v in a.items():
        if k in ("_FillValue", "missing_value", "actual_range", "NAVO_code", "time_origin"):
            continue
        out[k] = v[0] if isinstance(v, list) and len(v) == 1 else v
    return out


def var_attrs(h: dict, v: str) -> dict:
    a = _clean(h["vars"][v]["attrs"])
    a["units"] = "m s-1"
    # xarray (2026.2, zarr v3) does not mask with the array's fill_value by itself; the CF attribute does.
    a["missing_value"] = -30000
    return a


GLOBAL_KEEP = ("institution", "source", "field_type", "grid_name", "generating_model", "input_data_source",
               "reference", "operational_status", "distribution_statement")

METADATA_REPAIRS = (
    "water_u/water_v: units m/s -> m s-1; per-file actual_range and NAVO_code dropped; int16 values kept "
    "with scale_factor 0.001, add_offset 0; fill -30000 kept as the Zarr fill_value and as missing_value "
    "(decoded by readers). time/tau replaced by forecast_reference_time/lead/valid_time (checked against the filename "
    "in every file). depth/lat/lon stored once (identical in every file). Conventions CF-1.0 NAVO_netcdf_v1.0 "
    "-> CF-1.8. Per-file history/created_on/time_origin dropped."
)


def plan(files: list[SourceFile]):
    runs = np.array(sorted({f.run for f in files}), "datetime64[ns]")
    ri = {r: i for i, r in enumerate(runs)}
    li = {int(h): i for i, h in enumerate(LEADS_H)}
    owner = {}
    for f in sorted(files, key=lambda f: f.run):  # later runs overwrite earlier ones
        owner[f.valid] = f
    t0, t1 = min(owner), max(owner)
    times = np.arange(t0, t1 + np.timedelta64(3, "h"), np.timedelta64(3, "h")).astype("datetime64[ns]")
    best = [owner.get(t) for t in times]
    return runs, ri, li, times, best


def repo_config() -> ic.RepositoryConfig:
    cfg = ic.RepositoryConfig.default()
    cfg.set_virtual_chunk_container(ic.VirtualChunkContainer(url_prefix=PREFIX, store=ic.http_store()))
    # 520 refs per (variable, time step): split manifests every 8 runs (/forecast, ~270k refs) and
    # every 240 steps (/best, ~125k refs) so readers of recent data load only recent references.
    cfg.manifest = ic.ManifestConfig(splitting=ic.ManifestSplittingConfig.from_dict(
        {ic.ManifestSplitCondition.AnyArray(): {
            ic.ManifestSplitDimCondition.DimensionName("forecast_reference_time"): 8,
            ic.ManifestSplitDimCondition.DimensionName("time"): 240}}))
    # Small in-memory caches: with the defaults the full build was killed by this machine's 3.9 GB
    # memory limit after ~100 of 233 runs (2026-11-19), because cached references grow with every commit.
    cfg.caching = ic.CachingConfig(num_chunk_refs=200_000, num_transaction_changes=200_000,
                                   num_snapshot_nodes=50_000)
    return cfg


def open_repo(path: str, create: bool) -> ic.Repository:
    storage = ic.local_filesystem_storage(path)
    auth = {PREFIX: HTTP_ACCESS}
    if ic.Repository.exists(storage):
        repo = ic.Repository.open(storage, config=repo_config(), authorize_virtual_chunk_access=auth)
    elif create:
        repo = ic.Repository.create(storage, config=repo_config(), authorize_virtual_chunk_access=auth)
    else:
        raise FileNotFoundError(path)
    repo.save_config()
    return repo


def write_skeleton(store, files, scans, runs, times, best, grid, h) -> None:
    """Coordinates (materialized) and empty full-size u/v arrays for both groups."""
    depth, lat, lon = grid
    lead = (LEADS_H * np.timedelta64(1, "h")).astype("timedelta64[ns]")
    have = {(f.run, f.lead_h) for f in files}
    n_leads = np.array([sum((r, int(x)) in have for x in LEADS_H) for r in runs], np.int16)
    g = {k: h["gattrs"][k] for k in GLOBAL_KEEP if k in h["gattrs"]}
    common = dict(Conventions="CF-1.8", title="HYCOM 2.2.99+CICE Currents (ESPC-D V02, GLBy0.08 on GLBz0.04 latitudes)",
                  source_archive=PREFIX, source_format="NetCDF-3 classic, uncompressed big-endian int16, "
                  "referenced in place (virtual Icechunk), sub-chunked to 1 x 1 x 327 x 4500",
                  source_file_count=len(files), metadata_repairs=METADATA_REPAIRS, **g)
    base = {
        "depth": ("depth", depth, _clean(h["vars"]["depth"]["attrs"])),
        "lat": ("lat", lat, _clean(h["vars"]["lat"]["attrs"])),
        "lon": ("lon", lon, _clean(h["vars"]["lon"]["attrs"])),
    }
    fc = xr.Dataset(coords=base | dict(
        forecast_reference_time=("forecast_reference_time", runs,
                                 {"long_name": "Forecast run start (12 UTC)", "standard_name": "forecast_reference_time"}),
        lead=("lead", lead, {"long_name": "Hours after forecast_reference_time (tau)", "standard_name": "forecast_period"}),
        valid_time=(("forecast_reference_time", "lead"), runs[:, None] + lead[None, :],
                    {"long_name": "Valid time", "standard_name": "time"}),
        n_leads=("forecast_reference_time", n_leads,
                 {"long_name": "Number of the 65 leads present in the source archive for this run",
                  "comment": "Missing leads read as NaN"}),
    ), attrs=common | {"layout": "forecast"})
    nat = np.datetime64("NaT", "ns")
    bt = xr.Dataset(coords=base | dict(
        time=("time", times, {"long_name": "Valid time", "standard_name": "time", "axis": "T"}),
        forecast_reference_time=("time", np.array([f.run if f else nat for f in best], "datetime64[ns]"),
                                 {"long_name": "Run that supplies this time step", "standard_name": "forecast_reference_time"}),
        lead=("time", np.array([f.lead_h if f else -1 for f in best]).astype("timedelta64[h]").astype("timedelta64[ns]"),
              {"long_name": "Lead of the supplying run (-1 h: no file for this time)", "standard_name": "forecast_period"}),
    ), attrs=common | {"layout": "best", "missing_steps": int(sum(f is None for f in best))})
    for name, ds, dims, shape in (
        ("forecast", fc, ("forecast_reference_time", "lead", "depth", "lat", "lon"), (len(runs), len(LEADS_H), NZ, NY, NX)),
        ("best", bt, ("time", "depth", "lat", "lon"), (len(times), NZ, NY, NX)),
    ):
        for k, v in ds.coords.items():
            e = {"chunks": v.shape}
            if np.issubdtype(v.dtype, np.datetime64):
                e |= {"units": "hours since 2000-01-01 00:00:00", "calendar": "proleptic_gregorian", "dtype": "float64"}
            elif np.issubdtype(v.dtype, np.timedelta64):
                e |= {"units": "hours", "dtype": "int32"}
            ds[k].encoding = e
        ds.to_zarr(store, group=name, mode="w", zarr_format=3, consolidated=False)
        grp = zarr.open_group(store, path=name, mode="a")
        non_dim = " ".join(k for k in ds.coords if k not in ds.dims)
        for v in VARS:
            a = var_attrs(h, v)
            if non_dim:
                a["coordinates"] = non_dim
            grp.create_array(v, shape=shape, chunks=(*(1,) * (len(shape) - 2), CY, NX),
                             dtype="int16", fill_value=-30000, compressors=None, filters=None,
                             serializer=zarr.codecs.BytesCodec(endian="big"), dimension_names=dims, attributes=a)


def _specs(f: SourceFile, s: dict, var: str, lead_index: tuple[int, ...]) -> list:
    begin = s["begin_u"] if var == "water_u" else s["begin_v"]
    n = CY * NX * 2
    out = []
    for k in range(NZ):
        for j in range(NY // CY):
            out.append(ic.VirtualChunkSpec(index=[*lead_index, k, j, 0], location=f.url,
                                           offset=begin + (k * NY + j * CY) * NX * 2, length=n,
                                           etag_checksum=s["etag"]))
    return out


def _rss_mb() -> float:
    with open("/proc/self/status") as f:
        return next(int(x.split()[1]) for x in f if x.startswith("VmRSS")) / 1024


def write_refs(repo_path, branch, files, scans, runs, ri, li, best, batch_runs=10, batch_steps=200) -> str:
    """One commit per batch; the repository is reopened for every batch so its caches are released."""
    snap = None
    by_run: dict = {}
    for f in files:
        by_run.setdefault(f.run, []).append(f)
    rs = list(runs)
    for b in range(0, len(rs), batch_runs):
        chunk = rs[b:b + batch_runs]
        repo = open_repo(repo_path, create=False)
        session = repo.writable_session(branch)
        for v in VARS:
            specs = [s for r in chunk for f in by_run.get(r, []) for s in _specs(f, scans[f.path], v, (ri[r], li[f.lead_h]))]
            failed = session.store.set_virtual_refs(f"forecast/{v}", specs, validate_containers=True)
            if failed:
                raise RuntimeError(f"{len(failed)} references rejected for forecast/{v}")
        snap = session.commit(f"forecast refs, runs {str(chunk[0])[:10]} .. {str(chunk[-1])[:10]}")
        log.info("forecast runs %d-%d of %d committed (%s), RSS %.0f MB", b + 1, b + len(chunk), len(rs), snap, _rss_mb())
        del session, repo
    for b in range(0, len(best), batch_steps):
        if not any(best[b:b + batch_steps]):
            continue  # no file supplies any step in this batch (inside a gap)
        repo = open_repo(repo_path, create=False)
        session = repo.writable_session(branch)
        for v in VARS:
            specs = [s for i in range(b, min(b + batch_steps, len(best))) if best[i]
                     for s in _specs(best[i], scans[best[i].path], v, (i,))]
            failed = session.store.set_virtual_refs(f"best/{v}", specs, validate_containers=True)
            if failed:
                raise RuntimeError(f"{len(failed)} references rejected for best/{v}")
        snap = session.commit(f"best refs, steps {b} .. {min(b + batch_steps, len(best)) - 1}")
        log.info("best steps %d-%d of %d committed (%s), RSS %.0f MB", b, min(b + batch_steps, len(best)) - 1,
                 len(best), snap, _rss_mb())
        del session, repo
    return snap


# ------------------------------------------------------------------ validation
def open_readonly(path: str, group: str, snapshot=None) -> xr.Dataset:
    repo = ic.Repository.open(ic.local_filesystem_storage(path), authorize_virtual_chunk_access={PREFIX: HTTP_ACCESS})
    s = repo.readonly_session(snapshot_id=snapshot) if snapshot else repo.readonly_session("main")
    return xr.open_zarr(s.store, group=group, consolidated=False, chunks=None, zarr_format=3, mask_and_scale=False)


def _retry(fn, tries=4):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                raise
            log.warning("read failed (%s), retrying", str(e).splitlines()[0][:150])
            time.sleep(5 * (i + 1))


def _src_rows(f: SourceFile, s: dict, var: str, k: int, j0: int, n: int) -> np.ndarray:
    begin = s["begin_u"] if var == "water_u" else s["begin_v"]
    off = begin + (k * NY + j0) * NX * 2
    buf, _ = _get(f.url, {"Range": f"bytes={off}-{off + n * NX * 2 - 1}"})
    return np.frombuffer(buf, ">i2").reshape(n, NX)


def validate(path, files, scans, runs, ri, li, times, best, seed=0) -> None:
    import netCDF4

    F = open_readonly(path, "forecast")
    B = open_readonly(path, "best")
    assert F.water_u.dtype == np.int16 and F.water_u.encoding.get("chunks", (1, 1, 1, CY, NX))[-2:] == (CY, NX)
    assert (F.forecast_reference_time.values == runs).all() and (B.time.values == times).all()
    rng = np.random.default_rng(seed)
    by_era = sorted(files, key=lambda f: f.run)
    pick = {by_era[0].path, by_era[-1].path}
    pick |= {files[i].path for i in rng.choice(len(files), 3, replace=False)}
    first_2026 = [f for f in by_era if f.run >= np.datetime64("2026-08-20")]  # second producer era
    if first_2026:
        pick.add(first_2026[0].path)
    fs = {f.path: f for f in files}
    for p in sorted(pick):
        f, s = fs[p], scans[p]
        v = VARS[rng.integers(2)]
        k, j = int(rng.integers(NZ)), int(rng.integers(NY // CY))
        got = _retry(lambda: F[v].isel(forecast_reference_time=ri[f.run], lead=li[f.lead_h], depth=k,
                                       lat=slice(j * CY, j * CY + 3)).values)
        with netCDF4.Dataset(f.url + "#mode=bytes") as d:
            d.set_auto_maskandscale(False)
            ref = _retry(lambda: d[v][0, k, j * CY:j * CY + 3, :])
        np.testing.assert_array_equal(got, ref)
        log.info("source match (netCDF4): %s %s depth %d rows %d-%d", p, v, k, j * CY, j * CY + 2)
    # last chunk of the last variable in a file (end of file), via plain byte ranges
    f = by_era[-1]
    got = _retry(lambda: F.water_u.isel(forecast_reference_time=ri[f.run], lead=li[f.lead_h], depth=NZ - 1,
                                        lat=slice(NY - 2, NY)).values)
    np.testing.assert_array_equal(got, _src_rows(f, scans[f.path], "water_u", NZ - 1, NY - 2, 2))
    # a padded slot reads as fill
    have = {(f.run, f.lead_h) for f in files}
    pad = next(((r, int(h)) for r in runs for h in LEADS_H if (r, int(h)) not in have), None)
    if pad:
        x = F.water_v.isel(forecast_reference_time=ri[pad[0]], lead=li[pad[1]], depth=0, lat=slice(0, 2)).values
        assert (x == -30000).all(), "padded slot is not fill"
    # best == forecast for the last step
    lb = best[-1]
    a = _retry(lambda: B.water_u.isel(time=-1, depth=0, lat=slice(2600, 2603)).values)
    b = _retry(lambda: F.water_u.isel(forecast_reference_time=ri[lb.run], lead=li[lb.lead_h], depth=0,
                                      lat=slice(2600, 2603)).values)
    np.testing.assert_array_equal(a, b)
    log.info("validation OK: %d files compared with the source, end-of-file chunk, padding, best/forecast", len(pick))


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(HERE.parent / "hycom_icechunks"))
    ap.add_argument("--runs", type=int, default=0, help="smoke test: keep only the first and last N runs")
    ap.add_argument("--workers", type=int, default=6, help="parallel header range requests")
    ap.add_argument("--scan-only", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    t0 = time.time()

    files = discover()
    log.info("discovered %d files", len(files))
    if args.runs:
        rs = sorted({f.run for f in files})
        keep = set(rs[:args.runs] + rs[-args.runs:])
        files = [f for f in files if f.run in keep]
        log.info("smoke test: %d runs, %d files", len(keep), len(files))
    scans = scan(files, args.workers)
    grids = {s["grid"] for s in scans.values()}
    if len(grids) != 1:
        raise ValueError(f"depth/lat/lon differ between files: {grids}")
    log.info("all %d files share one grid (%s); %.0fs", len(files), grids.pop(), time.time() - t0)
    if args.scan_only:
        return 0

    runs, ri, li, times, best = plan(files)
    newest = max(files, key=lambda f: (f.run, f.valid))
    depth, lat, lon, h = read_grid(newest)
    repo = open_repo(args.repo, create=True)
    base = repo.lookup_branch("main")
    branch = f"build-{time.strftime('%Y%m%dT%H%M%S')}"
    repo.create_branch(branch, snapshot_id=base)
    session = repo.writable_session(branch)
    write_skeleton(session.store, files, scans, runs, times, best, (depth, lat, lon), h)
    session.commit(f"layout: {len(runs)} runs, {len(times)} best steps")
    del session
    snap = write_refs(args.repo, branch, files, scans, runs, ri, li, best)
    repo = open_repo(args.repo, create=False)
    repo.reset_branch("main", snap, from_snapshot_id=base)
    repo.delete_branch(branch)
    last_day = str(times[-1])[:10]
    log.info("main -> %s: %d runs %s .. %s, %d files, best %s .. %s (%d steps, %d missing)", snap, len(runs),
             str(runs[0])[:10], str(runs[-1])[:10], len(files), str(times[0])[:13], str(times[-1])[:13],
             len(times), sum(b is None for b in best))

    zarr.config.set({"async.concurrency": 32})
    validate(args.repo, files, scans, runs, ri, li, times, best)
    tag = f"data-through-{last_day}"
    if tag not in repo.list_tags():
        repo.create_tag(tag, snap)
    log.info("done in %.0fs; snapshot %s, tag %s", time.time() - t0, snap, tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
