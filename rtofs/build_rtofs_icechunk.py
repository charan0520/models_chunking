"""Build or refresh the RTOFS virtual Icechunk store.

    python build_rtofs_icechunk.py [--repo ../rtofs_icechunks] [--workers 4] [--dry-run] [--retry-unreadable]

Each run re-lists the source archive, opens only files that are new or whose listing
modification time changed (per-file references are cached in .ref_cache/), rebuilds /forecast
and /best, commits one snapshot, checks it from the consumer read path, and tags it
`data-through-<last valid day>`. Rerun daily; earlier snapshots stay readable by ID or tag.
The first build opens all ~3,700 files (about 2 s each, 4 workers: roughly 30-45 minutes).

Writes source_manifest.csv (every discovered file and the decision made for it) next to this script.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

import netCDF4
import numpy as np
import zarr

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import rtofs_build as rb  # noqa: E402

WIN = dict(Depth=slice(0, 3), Y=slice(1733, 1912), X=slice(2349, 2573))  # Gulf of Mexico, 0-20 m


def _retry(fn, tries: int = 4):
    """Source reads can fail with a transient 'connection closed before message completed' from
    TAMU-CC (seen 2026-11-18); virtual chunk reads have no retry of their own."""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - re-raised after the last attempt
            if i == tries - 1:
                raise
            logging.warning("read failed (%s), retrying", str(e).splitlines()[0][:120])
            time.sleep(5 * (i + 1))


def _vals(da) -> np.ndarray:
    return _retry(lambda: da.values)


def _src(f: rb.SourceFile) -> np.ndarray:
    def read():
        with netCDF4.Dataset(f.url + "#mode=bytes") as d:
            return d[f.var][0, WIN["Depth"], WIN["Y"], WIN["X"]].filled(np.nan)
    return _retry(read)


def validate(repo_path: str, refs: list[rb.FileRef], bad: list[rb.Unreadable], seed: int = 0) -> None:
    """Consumer read path: completeness against the listing, then real-data comparisons with the
    source at every transition boundary, the first and last runs, and two random files."""
    F = rb.open_readonly(repo_path, "forecast", chunks={})
    B = rb.open_readonly(repo_path, "best", chunks={})
    runs = sorted({r.src.run for r in refs} | {b.src.run for b in bad})
    assert list(F.forecast_reference_time.values) == runs, "runs in store != runs in listing"
    assert (np.diff(B.time.values) == np.timedelta64(1, "D")).all()
    assert -180 <= float(F.Longitude.min()) and float(F.Longitude.max()) < 180

    by_run: dict = {}
    for r in refs:
        by_run.setdefault(r.src.run, []).append(r)
    exp = np.array([by_run[r][0].experiment if r in by_run else "" for r in runs])
    pick = {runs[0], runs[-1]}
    for i in range(1, len(runs)):  # both sides of every experiment / lead-count change
        a, b = by_run.get(runs[i - 1], []), by_run.get(runs[i], [])
        if exp[i] != exp[i - 1] or max(x.src.lead for x in a) != max(x.src.lead for x in b):
            pick |= {runs[i - 1], runs[i]}
    rng = np.random.default_rng(seed)
    pick |= {runs[j] for j in rng.choice(len(runs), 2, replace=False)}
    for run in sorted(pick):
        f = max(by_run[run], key=lambda r: (r.src.lead, r.src.var)).src  # last lead: crosses padding
        a = _vals(F[f.var].sel(forecast_reference_time=f.run, lead=np.timedelta64(f.lead, "D")).isel(**WIN))
        np.testing.assert_array_equal(a, _src(f))
        logging.info("source match: %s", f.name)
        n = int(F.n_leads.sel(forecast_reference_time=run))
        if n < rb.N_LEAD:
            pad = _vals(F.u.sel(forecast_reference_time=run).isel(lead=rb.N_LEAD - 1, Depth=0,
                                                                   Y=slice(1800, 1810), X=slice(2450, 2460)))
            assert np.isnan(pad).all(), f"padding of {run} is not NaN"
    for b in bad:
        x = F[b.src.var].sel(forecast_reference_time=b.src.run, lead=np.timedelta64(b.src.lead, "D"))
        assert np.isnan(_vals(x.isel(Depth=0, Y=slice(1800, 1810), X=slice(2450, 2460)))).all()
    last = B.isel(time=-1)
    assert last.forecast_reference_time.values == runs[-1]
    np.testing.assert_array_equal(
        _vals(last.u.isel(**WIN)),
        _vals(F.u.sel(forecast_reference_time=runs[-1]).isel(lead=-1, **WIN)))
    logging.info("validation OK: %d runs compared with the source, best/forecast consistent", len(pick))


def write_manifest(files, refs, bad, best_owner: set) -> None:
    ok = {r.src.path for r in refs}
    why = {b.src.path: b.reason for b in bad}
    with open(HERE / "source_manifest.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "listed_mtime", "listed_size", "run", "valid", "lead_days", "variable",
                    "experiment", "decision"])
        exp = {r.src.path: r.experiment for r in refs}
        for s in files:
            if s.path in ok:
                d = "forecast+best" if (s.run, s.lead) in best_owner else "forecast"
            else:
                d = f"excluded (unreadable HDF5: {why[s.path]})"
            w.writerow([s.path, s.listed_mtime, s.listed_size, str(s.run)[:10], str(s.valid)[:10], s.lead,
                        s.var, exp.get(s.path, ""), d])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(HERE.parent / "rtofs_icechunks"))
    ap.add_argument("--workers", type=int, default=4, help="parallel header reads against the source")
    ap.add_argument("--dry-run", action="store_true", help="discover, open and assemble, but do not write")
    ap.add_argument("--validate-only", action="store_true",
                    help="validate and tag the current main without writing (e.g. after a validation read failed)")
    ap.add_argument("--retry-unreadable", action="store_true",
                    help="re-open files cached as unreadable even if their modification time is unchanged")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    t0 = time.time()

    files, other = rb.discover()
    logging.info("discovered %d files; %d entries not matching the pattern: %s", len(files), len(other), other)
    if not files:
        raise SystemExit("no source files found")
    rb.check_listing(files)

    refs, bad = rb.open_files(files, workers=args.workers, retry_unreadable=args.retry_unreadable)
    rb.check_compatible(refs)
    T = rb.load_templates(refs, rb.make_registry())
    logging.info("%d readable, %d unreadable (%.0fs)", len(refs), len(bad), time.time() - t0)

    fc = rb.build_forecast(refs, T, variables=())  # coordinates only; write() assembles u/v one at a time
    best = rb.build_best(refs, T, variables=())
    _, owners = rb.best_slots(refs)
    write_manifest(files, refs, bad, {o for o in owners if o})
    logging.info("forecast %s", dict(fc.sizes) | {"lead": rb.N_LEAD})
    logging.info("best %s, %d days with no run", dict(best.sizes), best.attrs["missing_days"])
    if args.dry_run:
        return 0

    repo = rb.open_or_create(args.repo)
    runs = fc.forecast_reference_time.values
    last_day = str(best.time.values[-1])[:10]
    msg = (f"{len(runs)} runs {str(runs[0])[:10]} to {str(runs[-1])[:10]}, valid to {last_day}; "
           f"{len(refs)} files referenced, {len(bad)} unreadable excluded")
    if args.validate_only:
        snap = repo.lookup_branch("main")
        logging.info("validating existing main %s", snap)
    else:
        snap = rb.write(repo, refs, T, msg)
        logging.info("committed %s: %s", snap, msg)

    zarr.config.set({"async.concurrency": 32})
    validate(args.repo, refs, bad)

    tag = f"data-through-{last_day}"
    if tag not in repo.list_tags():
        repo.create_tag(tag, snap)
        logging.info("tagged %s", tag)
    logging.info("done in %.0fs; snapshot %s", time.time() - t0, snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
