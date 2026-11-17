# Handoff — 2026-11-17

## Where things stand

- Surveyed the NOAA ECCOFS archive `s3://noaa-nos-eccofs-pds` (metadata only). Results are in
  [docs/eccofs-source-survey.md](docs/eccofs-source-survey.md).
- Proposed store layout and open questions are in [docs/design-decisions.md](docs/design-decisions.md).
  The proposal is one repository at `eccofs_icechunks/` with groups `/avg`, `/his`, `/qck` and `/sta`, and the inputs excluded.
- No build code written yet. No Icechunk store created yet.
- This work is on branch `docs/eccofs-survey-and-handoff`, in a PR to `main` awaiting the owner's approval.
  The team's design questions are in the PR description.

## FIRST THING next session: check GitHub for responses

Before any planning or code, read the team's replies:

```bash
gh pr list --head docs/eccofs-survey-and-handoff --state all
gh pr view <PR#> --comments          # replies to the design questions
gh api repos/charan0520/eccofs_demo/pulls/<PR#>/comments   # inline review comments
gh issue list                        # in case the team opened an issue instead
```

- Fold the answers into `docs/design-decisions.md`, marking each decision as **decided** with who and when.
- If questions are still unanswered, tell the owner and don't guess. The owner asked to wait for the team's responses before starting.
- If the PR is merged, branch from the updated `main`. Ask the owner before deleting the merged branch.

## Next task: plan the first Icechunk (easiest example)

Follow the virtual-icechunk skill order: plan → smoke-test notebook → production → docs.

Candidate first example: `/avg` for 2–3 cycles after 2026-07-27.
It's HDF5, uncompressed, with sensible chunks. Steps:

1. Check that `avg` files before and after 2026-07-27 have identical variables, chunks and dtypes.
   (File size changed on 2026-07-27.)
2. Write the plan for the time layout using the team's answer. Default proposal:
   - `forecast_reference_time` taken from the filename;
   - `lead` taken from the file index `_000N`.
3. Smoke-test notebook against a **temporary** local repo, not `eccofs_icechunks/`:
   - parser: VirtualiZarr HDF parser;
   - load `ocean_time` and the grid coordinates;
   - write time as one chunk;
   - authorize the `s3://noaa-nos-eccofs-pds/` virtual chunk container (anonymous) and save the config;
   - reopen read-only and read real `temp` and `zeta` values.

## Things to remember

- **Git workflow:**
  - branch per change, never push to `main`;
  - PR with a summary, wait for the owner's approval before merging;
  - ask before deleting branches.
- **Formats:** `his/`, `qck/` and `sta/` are **NetCDF3**; `avg/`, `frc/`, `obs/` and `riv/` are HDF5. They need different parsers.
- **`his/` chunk size:** 480 MB single-chunk 3D variables. Flag this to readers, and check whether the NetCDF3 parser can sub-split.
- **Retention:** `frc/`, `obs/` and `riv/` may be on a 21-day rolling window. Re-list the bucket to confirm (they started on 2026-10-24 at survey time).
- **Creating issues:** creating a GitHub issue with `gh issue create` was blocked by the permission system in this session.
  Use the PR thread, or ask the owner to allow it.
- **Versions:** Python 3.13.18, virtualizarr 2.8.0, icechunk 2.3.1, zarr 3.4.2, xarray 2026.9.0.
