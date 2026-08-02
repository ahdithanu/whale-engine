# Whale Engine

Facility-level account intelligence for a robotic surface finishing company.
Full context and working decisions live in [`CLAUDE.md`](CLAUDE.md) — this
file is the short version: what's real, what's mocked, and what to watch out
for.

## What's real vs. what's architecture

| Stage | Question | Status |
|---|---|---|
| 1. Facility Signal Engine | Which plant first | **REAL** — this is the build |
| 2. Part Fit Qualifier | Will the robot work here | Mocked |
| 3. Multithread Map | Who else needs to be in the deal | Mocked |
| 4. Capital Case Builder | How does this clear a committee | Mocked |
| 5. Expansion Engine | How does one cell become thirty | Mocked |

Agent 1 is real, end to end: bulk ingestion from EPA FRS/NEI, OSHA ITA, and
USASpending; a two-stage entity resolver (facility, then corporate); and a
weighted scoring model producing a 0–100 signal score per facility. Everything
downstream of Agent 1 is architecture, not a working pipeline.

## Running it

```
make setup          # uv sync + npm install
make ingest          # epa/osha/dod (each to its own DuckDB file) + merge
make resolve          # full-universe entity resolution -> facilities/accounts tables
uv run python -m pipeline.resolve.report   # anchor-scoped resolver report (Boeing/Oshkosh/Caterpillar)
make score            # scoring model -> facility scores, why-now strings, account rollups
```

## Known gaps — not hidden, not yet fixed

- **Federal facility coverage is likely undercounted.** The 12 named federal
  depots (Air Force Air Logistics Complexes, naval shipyards, Army depots,
  Marine Corps Logistics Bases — see `pipeline/overrides/federal_depots.yaml`)
  resolve into far fewer *qualified* facilities than their real finishing
  operations would suggest (e.g. U.S. Air Force: 2 of 25 resolved facilities
  qualify). Two structural reasons, not a resolver bug: federal agencies
  report OSHA injury data under a different regime than the bulk ITA CSV
  covers, and large installations often report EPA emissions at the
  installation level rather than per shop. See CLAUDE.md's "Federal facility
  coverage is likely undercounted" section for the full explanation. Worth a
  dedicated fix later — deliberately not attempted yet.
- **Full-universe entity resolution is coarser than the proven anchor run.**
  Accounts form by exact normalized-name match plus hand-curated aliases in
  `pipeline/overrides/corporate_map.yaml`; real companies with sub-facility
  naming variance (`"CATERPILLAR INC"` vs `"OSHKOSH CORP - WEST PLT"`)
  fragment into separate accounts until an alias is added. This undercounts
  `account_is_customer` facility/qualified totals for named customers — a
  known, accepted limitation, not a bug to chase down blind.
- **DoD award data is pre-completeness-filter.** `dod_awards` reflects an
  earlier ingestion run known to undercount high-volume NAICS codes; the
  minimum-award-amount filter that fixes this is designed but not yet built.
  Don't treat DoD dollar figures as final.
- **Subaward data is not ingested.** Only prime USASpending awards. Tier 1/2
  suppliers whose revenue is mostly subcontracted through a prime will look
  artificially cold on the DoD signal — expected, not a bug.

## Repo layout

```
/pipeline    Python. Ingestion, entity resolution, scoring. uv + Polars + DuckDB + httpx.
/web         Next.js 15 app router, TypeScript, Tailwind.
/data        Gitignored. Raw downloads and the DuckDB files.
```
