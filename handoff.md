# Handoff — 2026-11-18

Repository: `charan0520/models_chunking` (private; replaces the deleted `eccofs_demo` repo). Local folder is
still `/home/jovyan/eccofs_demo`. Three datasets, at different stages:

| Dataset | Stage | Where |
|---|---|---|
| **RTOFS** (TAMU-CC global u/v) | Store built and validated; **PR #2 awaiting approval** | branch `feat/rtofs-virtual-icechunk`, `rtofs/` |
| **TXLA** (TAMU-CC ROMS) | Store built; **merged** to `main` (PR #1, 2026-11-18) | `txla/` |
| **ECCOFS** (NOAA, S3) | Survey and design only; **team questions not yet re-posted** | `docs/` |

## FIRST THING next session

```bash
git fetch origin && git switch main && git pull
gh pr view 2 --comments                 # RTOFS: approval or review comments?
gh pr view 2 --json mergeable --jq .mergeable
```

- **PR #2** had a `.gitignore` conflict with the merged TXLA work; I merged `origin/main` into the branch
  and pushed (`473be8b`). GitHub now reports it `MERGEABLE`.
- Do **not** merge PR #2 without the owner's approval. Ask before deleting `feat/txla-virtual-icechunk`
  (merged) or any other branch.

## RTOFS — what was done today

Virtual store of `https://hriai01.tamucc.edu/data/MASTR_MODELS/rtofs/history/` in `rtofs_icechunks/`
(local, git-ignored, ~165 MB after two builds). Full details: `rtofs/README.md`, `rtofs/source-audit.md`.

- `/forecast` (267 runs × 8 lead days × 33 depths × 3298 × 4500, u and v) and `/best` (527 daily steps,
  2025-06-17 … 2026-11-25). 3,680 files referenced, 2 unreadable excluded.
- `main` at snapshot `XKG4RQMNKYYD6DM5S47G`, tag `data-through-2026-11-25`.
- Owner decisions: pad 5-/4-day runs to 8 leads (NaN); keep model versions 93.0 and 93.1, tagged by an
  `experiment` coordinate; wrap longitude to [-180, 180).
- Validated against the source in 12 runs covering every transition; smoke test `rtofs/smoke_test.ipynb`
  passes.

**Refresh daily:** `conda run -n notebook python -I rtofs/build_rtofs_icechunk.py` (~2 min; per-file
references are cached in `rtofs/.ref_cache/`, so only new files are opened). A first build from an empty
cache takes ~30 min.

**Lessons that are easy to trip over again**

- This machine's memory limit is **3.9 GB** (cgroup), not the 30 GB `free` shows. Writing all four RTOFS
  arrays in one session was killed (exit 137); the build now commits one array at a time on a temporary
  Icechunk branch and then resets `main`.
- `conda run` does **not** pass stdin: `conda run ... python - <<EOF` silently runs nothing. Write the script
  to a file first.
- `python -I` drops the script's own folder from `sys.path`; use it for scripts that read downloaded files,
  not for scripts importing sibling modules.
- TAMU-CC occasionally drops connections mid-read ("connection closed before message completed"). Retry; it
  is not corruption.
- `to_zarr(mode="a")` will not resize existing arrays; the build deletes them first and keeps lat/lon.

**RTOFS open items**

1. **Snapshot growth**: each refresh adds ~53 MB (~19 GB/year if all kept). Expiring old snapshots needs an
   owner decision on which tags to keep.
2. **Questions for TAMU-CC** (in `rtofs/source-audit.md`), not sent yet: the two unreadable 2025-07-29 v
   files, the 2025-12-11 … 2026-08-19 gap, daily mean vs 00 UTC snapshot, why only u/v, rewritten
   2025-10-01 files, retention.
3. Browser access untested (TAMU-CC sent no CORS header in the TXLA audit).

## TXLA

Merged. Refresh with `python txla/build_txla_icechunk.py`. Its questions for TAMU-CC are in
`txla/source-audit.md` and have not been asked; they overlap with the RTOFS ones, so send them together.

## ECCOFS

Nothing new today. The 7 design questions in `docs/design-decisions.md` ("Open decisions for the team")
were lost with the deleted `eccofs_demo` repo and its PR. They need re-posting on `models_chunking` (a PR
thread or an issue; `gh issue create` was blocked by the permission system last time, so ask the owner).
The owner wants the team's answers before any ECCOFS build work. The planned first store is still `/avg`
for a few cycles after 2026-07-27 (see the 2026-11-17 notes in git history of this file).

## Environment

Python 3.13.18 via `conda run -n notebook`; icechunk 2.3.1, virtualizarr 2.8.0, xarray 2026.9.0,
zarr 3.4.2, obstore 0.12.1, h5py 3.16.0, netCDF4 1.7.5 (`rtofs/requirements.txt`).
