"""Build or refresh the TXLA virtual Icechunk store.

    python build_txla_icechunk.py [--repo ../txla_icechunks] [--workers 4] [--dry-run]

Each run re-lists the source archive, re-reads every file header (metadata only, about
2 MB per file, no science data), rebuilds /forecast and /best, and commits one new
snapshot. Rerun daily to pick up new runs; earlier snapshots stay readable by ID.
It writes source_manifest.csv (the files and the decision made for each) next to this script.
"""

from __future__ import annotations

import argparse
import csv
import logging
import random
import sys
import time
from pathlib import Path

import netCDF4
import numpy as np
import zarr

sys.path.insert(0, str(Path(__file__).parent))
import txla_build as tb  # noqa: E402

HERE = Path(__file__).parent


def spot_check(repo_path: str, kept, n: int = 3, seed: int = 0) -> None:
    """Compare a few random (run, lead, variable) fields read through the store with the source."""
    F = tb.open_readonly(repo_path, "forecast")
    B = tb.open_readonly(repo_path, "best")
    rng = random.Random(seed)
    for _ in range(n):
        r = rng.choice(kept)
        j = rng.randrange(r.n)
        v = rng.choice(["zeta", "temp", "salt", "u"])
        a = F[v].sel(forecast_reference_time=r.frt, lead=np.timedelta64(j + 1, "h")).values
        with netCDF4.Dataset(r.url + "#mode=bytes") as d:
            b = d[v][j].filled(np.nan)
        np.testing.assert_array_equal(a, b)
        logging.info("spot check OK: %s run %s lead %d h", v, r.archived, j + 1)
    # the newest hour in /best comes from the newest run
    last = B.isel(time=-1)
    assert last.forecast_reference_time.values == kept[-1].frt
    np.testing.assert_array_equal(
        last.zeta.values, F.zeta.sel(forecast_reference_time=kept[-1].frt).isel(lead=-1).values
    )
    logging.info("best/forecast consistency OK")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(HERE.parent / "txla_icechunks"))
    ap.add_argument("--workers", type=int, default=4, help="parallel header reads against the source")
    ap.add_argument("--dry-run", action="store_true", help="discover and assemble, but do not write")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    t0 = time.time()
    urls, other = tb.discover()
    logging.info("discovered %d files; %d entries not matching the pattern: %s", len(urls), len(other), other)
    if not urls:
        raise SystemExit("no source files found")

    reg = tb.make_registry()
    runs = tb.open_runs(urls, reg, workers=args.workers)
    kept, excluded = tb.select_runs(runs)
    tb.check_compatible(kept)
    grid = tb.load_grid(kept, reg)
    logging.info("kept %d runs, excluded %d (%.0fs)", len(kept), len(excluded), time.time() - t0)

    with open(HERE / "source_manifest.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "archived_utc", "forecast_reference_time", "records", "decision"])
        why = dict(excluded)
        for r in sorted(runs, key=lambda r: r.archived):
            w.writerow([r.url.removeprefix(tb.SOURCE_PREFIX), r.archived, str(r.frt)[:19], r.n,
                        f"excluded: {why[r.url]}" if r.url in why else "kept"])

    fc = tb.build_forecast(kept, excluded, grid)
    best = tb.build_best(kept, excluded, grid)
    logging.info("forecast %s", dict(fc.sizes))
    logging.info("best %s, %d missing hours", dict(best.sizes), best.attrs["missing_hours"])
    if args.dry_run:
        return 0

    repo = tb.open_or_create(args.repo)
    msg = (f"{len(kept)} runs, {str(kept[0].frt)[:10]} to {str(kept[-1].frt)[:10]}; "
           f"{len(excluded)} files excluded")
    snap = repo_snapshot = tb.write(repo, fc, best, msg)
    logging.info("committed %s: %s", repo_snapshot, msg)

    zarr.config.set({"async.concurrency": 32})
    spot_check(args.repo, kept)
    logging.info("done in %.0fs; snapshot %s", time.time() - t0, snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
