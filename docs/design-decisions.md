# ECCOFS virtual Icechunk: proposed design and open decisions

Status: **proposal, awaiting team input** (2026-11-17). Facts are in [eccofs-source-survey.md](eccofs-source-survey.md).

## Destination

Local store under `eccofs_icechunks/` in this repository's working directory.
Open question: should `eccofs_icechunks/` be added to `.gitignore`? It holds generated metadata and should probably not be committed.

## Proposed partition

One repository, one group per model output product:

| Group | Source | Parser | Proposed dims | Notes |
|---|---|---|---|---|
| `/avg` | `avg/` | HDF5 | `forecast_reference_time × lead (8) × s × eta × xi` | Best performance. |
| `/his` | `his/` | NetCDF3 | `forecast_reference_time × lead (9) × s × eta × xi` | 480 MB virtual chunks; see the survey. |
| `/qck` | `qck/` | NetCDF3 | `forecast_reference_time × lead (3-hourly) × eta × xi` | Two layouts before and after 2026-07-27 to map onto one lead axis. Exclude `_fwd`. |
| `/sta` | `sta/` | NetCDF3 | `forecast_reference_time × lead (hourly) × station` | 96 vs 330 stations: subgroups, or start at 2026-08-03. |

Why groups and not one dataset: the products share a grid and model run, but their time axes, variable sets and file formats differ, so they cannot be concatenated or merged.
Why one repo and not four: they are one product, updated together once per cycle, and a reader wants them side by side.
Separate repos are a valid alternative if independent updates or sharing matter more.

Excluded for now: `frc/`, `obs/`, `riv/`. These are model inputs, likely kept on a rolling ~21-day window, and `obs` is not gridded.

## Open decisions for the team

1. **Time layout.** Choose one:
   - (a) full forecast collection with 2D time, run × lead (recommended);
   - (b) best-estimate series only (lead day 0/1 from each cycle);
   - (c) both.
2. **Scope of the first store.** Start with the easiest example (recommended: `/avg` only, a handful of cycles, as a smoke test), then add the others?
3. **Inputs.** Confirm `frc/`, `obs/` and `riv/` are excluded.
4. **Stations.** Keep both station eras, or start from 2026-08-03?
5. **Missing cycles.** Leave as NaN-filled gaps in the run dimension (recommended), or drop them?
6. **Reference URLs.** Embed `s3://noaa-nos-eccofs-pds/` (anonymous S3, fastest in us-east-1) or the `https://` form (works from anywhere and in browsers if CORS allows)?
7. **Updates.** Is a one-time build enough, or should the design plan for appending each new daily cycle?

## Easiest first example (candidate)

`/avg` for 2–3 cycles after 2026-07-27, i.e. 16–24 files:

- HDF5, uncompressed, sensible chunks.
- One time step per file, so time must be loaded and chunked as one.
- The forecast reference time comes from the filename and the lead from the file index.
- Smoke-test notebook against a temporary local repo. Reopen read-only and read a real slice of `temp` and `zeta`.

Still to verify before scaling: `avg` files from before and after 2026-07-27 have the same variables and chunk layout.
