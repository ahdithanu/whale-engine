# Whale Engine

Facility-level account intelligence for a robotic surface finishing company.

## What this is

The addressable universe for robotic surface finishing is a few hundred US
manufacturers. This is not a lead generation problem. It is a "which specific
plant inside which specific company do I walk into first" problem.

Public environmental, safety, and federal contracting data answers that question
at the facility level, and almost nobody in GTM uses it.

A $20M account is not one purchase. It is one logo, 8 to 15 plants, 2 to 4 cells
per plant, sequenced over 3 to 5 years. The revenue is created by the expansion,
not the first sale. Every module here exists to serve that motion.

## Repo layout

```
/pipeline    Python. Ingestion, entity resolution, scoring.
             uv + Polars + DuckDB + httpx. Tests in /pipeline/tests.
/web         Next.js 15 app router, TypeScript, Tailwind.
/data        Gitignored. Raw downloads and the DuckDB file.
```

Make targets: `setup`, `ingest` (`ingest-epa`, `ingest-osha`, `ingest-dod`, `merge`), `resolve`, `score`, `export`, `dev`.

## Core principles

**Precision on whales beats recall on the tail.** A rules-based approach with a
hand-curated override file that gets the top 200 accounts right is worth more
than an ML approach that gets 85 percent of everything right. Optimize
accordingly, everywhere.

**Every number must be traceable to a source.** No derived figure appears in the
UI without the raw underlying value and the dataset it came from being one click
away. Nothing should look invented.

**Manual override files are the correct answer, not a shortcut.** When
automation fails on messy real-world data, curate by hand and move on.

**Idempotent re-runs.** Cache raw downloads in /data/raw. Never re-download
gigabytes because a downstream step failed.

**Ask before inventing.** If an API endpoint, bulk file location, or schema is
uncertain, say so and ask. Do not build around a guessed endpoint.

## Target universe

Defined in `/pipeline/naics_universe.json`. 25 NAICS codes across 7 verticals.
Validated against the customer's publicly named customers and their seven
published applications: sanding, grinding, blasting, inspection, polishing,
buffing, spraying.

Verticals: Aerospace and defense; Aerospace MRO; Maritime; Specialty and heavy
vehicles; Heavy equipment and machinery; Plastics and composites; Metal
fabrication.

Do not substitute or "improve" these codes. They were validated deliberately.
(Plastics and composites — 326199, 326121, 326113 — added 2026-08-01: large
thermoformers and composite shops run heavy trim and sand labor. The
qualification gate filters for scale; the NAICS list doesn't need to.)

### Federal depots: named override, not a NAICS filter

Twelve facilities — Air Force Air Logistics Complexes (Tinker, Hill, Robins),
naval shipyards (Norfolk, Puget Sound, Pearl Harbor, Portsmouth), Army depots
(Anniston, Red River, Letterkenny), Marine Corps Logistics Bases (Albany,
Barstow) — are among the largest surface finishing operations in the country
but file under national-security codes outside the NAICS universe above. See
`/pipeline/overrides/federal_depots.yaml` and the "EPA FRS" data source
section below for how these are matched (name AND location, not name alone —
a bare word like "Tinker" collides with unrelated businesses in FRS).

`naics_universe.json` also carries `scope_questions` — wood millwork/cabinetry
and marine/RV gelcoat finishing — flagged as unresolved with the customer.
Ingestion deliberately excludes these codes until answered; do not fold them
into the 22 confirmed codes without checking first.

### Anchor accounts

Boeing, Oshkosh Corporation, Caterpillar. These are the customer's own named
customers. They serve two purposes:

1. Entity resolution ground truth. If the resolver cannot correctly group these
   three, it is not working, regardless of what aggregate metrics say.
2. Proof of the expansion thesis. All three are large multi-plant enterprises.

Caterpillar will stress the resolver hardest, since it operates under many
subsidiary names.

### High-mix is the target, not a disqualifier

The customer sells to HIGH-MIX manufacturers. Low volume across many part types
is the sweet spot, because that is precisely the segment fixed robotic
automation cannot serve.

Never treat low per-part volume as a fail condition. Qualify on total annual
finishing labor hours across the part mix. A plant running 400 low-volume part
types is a better target than one running two high-volume parts.

## Data sources

| Source | Provides | Notes |
|---|---|---|
| EPA FRS | Facility registry, address, NAICS, program IDs | Bulk download preferred over API |
| EPA NEI | Facility-level VOC and PM tonnage | Primary proxy for finishing operation scale |
| OSHA ITA | Establishment injury data, DART rate | Annual bulk CSV. Names are free text and messy |
| USASpending | DoD awards by place of performance | Place of performance is the field that matters, not recipient address |

### EPA FRS: bulk download, exact files

`https://ordsext.epa.gov/FLA/www3/state_files/national_combined.zip` (~1.26GB).
Bundles 11 CSVs; ingestion only extracts and uses two:
- `NATIONAL_NAICS_FILE.CSV` — REGISTRY_ID + NAICS_CODE, used to find our
  22-code universe. One row per (registry_id, program, naics); a facility
  matches if ANY row carries a target code.
- `NATIONAL_FACILITY_FILE.CSV` — name, address, lat/lon, and critically
  `PGM_SYS_ACRNMS`, a single column already carrying every program system ID
  as "ACRONYM:ID" pairs (e.g. `EIS:12663611, AIR:AK...`). This is why the much
  larger `NATIONAL_ENVIRONMENTAL_INTEREST_FILE.CSV` (1.1GB) and
  `NATIONAL_PROGRAM_FILE.CSV` (3.0GB) are not needed at all.

Not the `get_facilities` REST API — its docs don't confirm NAICS filtering
support, and pagination/rate-limit behavior is undocumented. Bulk sidesteps
the ambiguity entirely and is the officially sanctioned path.

**Title V permit proxy:** FRS has no direct "Title V" flag we ingest, but
`NATIONAL_NAICS_FILE.CSV` rows with `PGM_SYS_ACRNM = 'AIR'` carry an
`INTEREST_TYPE` of `AIR MAJOR` / `AIR MINOR` / `AIR SYNTHETIC MINOR` —
confirmed against real data (19,516 AIR MAJOR rows in our NAICS set).
`AIR MAJOR` is the closest available signal to "requires a Title V permit"
without a separate permit-database join, and is what
`epa_facilities.has_air_major_permit` is built from.

**Federal depot override:** `epa.py` also matches
`/pipeline/overrides/federal_depots.yaml`'s 12 named facilities directly
against `NATIONAL_FACILITY_FILE.CSV` by name AND location, unioning their
registry IDs in alongside the NAICS-matched set so they flow through the same
NEI-join and Title V pipeline below. Name alone is not safe: real FRS data
confirmed a bare word like "TINKER" also matches "Tinker Bell Cleaners" and
an entire Colorado gas station chain ("Stinker Stores"). Every `aka` entry in
the override file is a verified full multi-word phrase, checked against real
`NATIONAL_FACILITY_FILE.CSV` PRIMARY_NAME values before being trusted, and
the city/state check is a second independent gate. Confirmed 2026-08-01:
12/12 named depots matched, 90 FRS records total (most large depots have many
EPA sub-registrations — one project, one demolition, one solar array each —
same pattern the resolver already handles for Boeing/Caterpillar/Oshkosh).
`epa_facilities.federal_depot_account` / `federal_depot_category` are NULL
for everything else.

### The EPA FRS <-> NEI join: not registry_id-to-registry_id

NEI's facility key is an **EIS Facility ID**, not an FRS Registry ID. The real
join path, confirmed against real data:

```
NEI.eis_facility_id  ->  FRS.PGM_SYS_ACRNMS contains "EIS:<that id>"  ->  REGISTRY_ID
```

This is why FRS ingestion captures *every* program system ID per facility,
not just air-prefixed ones narrowly — the EIS entry inside that same field is
the literal join key to NEI. Facilities with no EIS entry in PGM_SYS_ACRNMS
don't join to NEI at all; `epa_facilities.matched_to_nei_2017` /
`matched_to_nei_2020` record this rather than silently leaving nulls.

### NEI: two years, not one — the COVID-year caveat

`https://gaftp.epa.gov/air/nei/nei_facility_summaries/{year}_NEI_Facility_summary.zip`
Confirmed real filenames via EPA's directory listing, one file per year back
to 2012.

**2020 alone is not a safe scoring input.** 2020 is COVID-distorted, and
commercial aerospace — our primary vertical — was the hardest-hit part of the
economy that year. Since VOC tonnage is the highest-weighted scoring signal
(0.30), using 2020 alone would silently penalize aerospace facilities for a
one-time demand collapse, not a real drop in finishing operation scale.

Ingestion pulls **2017 and 2020** (`NEI_YEARS` is a list in `epa.py`, not a
scalar — 2023 NEI is not yet published as of this writing, confirmed against
EPA's live status page still showing OAR review; adding it later is a
one-line change). Both years' VOC/PM10/PM25 tonnage are stored, plus a
computed `_pct_change_2017_2020` and a categorical `_trend` (`up`/`down`/
`flat`/`no_data`, ±5% band). Trend direction is treated as its own signal
from absolute tonnage — a facility growing VOC output despite the 2020 dip is
evidence of expanding finishing operations, not noise to average away.

Confirmed exact pollutant codes (all TONs, one row per facility per pollutant
per year, no aggregation needed): `VOC`, `PM10-PRI`, `PM25-PRI`. PM10 and
PM2.5 are stored separately, never summed — PM2.5 is a physical subset of
PM10, so summing would double-count and invent a figure EPA doesn't report.

### USASpending / DoD: prime awards only, city-level place of performance

`POST https://api.usaspending.gov/api/v2/search/spending_by_award/` — public,
unauthenticated JSON API. Filtered to prime contracts (award_type_codes
A/B/C/D), trailing 24 months, per target NAICS code.

**DoD place-of-performance granularity, confirmed empirically (2026-08-01):**
of the captured PoP fields, **0% have a street address; 100% have
city/county/state/zip only.** USASpending's "Primary Place of Performance"
object simply doesn't expose a street-level field — this isn't a data gap on
our end, it's the shape of the data. Consequence: DoD awards cannot be pinned
to one specific facility. They attach at the (account, city, state) level
instead — summed and attributed to whichever of that account's resolved
facilities sit in that city. If an account has more than one facility in a
city, the dollars are reported as a shared city-level signal across all of
them, never silently assigned to one. Scoring should treat DoD as an
account-and-city-level signal, not a facility-precise one.

**Prime-awards-only is a known, accepted limitation.** We do not ingest
subawards (FSRS data). Tier 1/2 suppliers whose revenue is mostly
subcontracted through a prime will look artificially cold on the DoD signal —
this is expected, not a bug, and subaward ingestion is deliberately not being
built right now. If a facility scores low on DoD dollars but everything else
about it looks like a whale, subcontracting is a plausible explanation worth
a human glancing at before writing the account off.

**Deep-pagination reliability, the hard way.** The search endpoint's
`hasNext` flag is not trustworthy near ~10,000 results — confirmed by
cross-checking against the separate `spending_by_award_count` endpoint: NAICS
336413 has 120,572 true awards; the search endpoint returned 9,939 and
claimed `hasNext: false`. Splitting a date window only when the *count*
endpoint says the true total exceeds a threshold (not when `hasNext` says so)
is the only reliable approach found. Unpaced, high-volume request bursts also
triggered sustained connection resets independent of pagination depth — a
fresh `httpx.Client` per NAICS code plus ~0.5s pacing per request resolved it.
Per-NAICS-code results are cached to `/data/raw/dod/` as soon as they
succeed, so a re-run only redoes codes that actually failed.

**Outstanding, not yet built:** a minimum-award-amount filter (default
$100K, configurable) to cut request volume on high-award-count codes like
336413 further, with a reported %-of-total-dollars-retained figure to defend
the threshold. The `dod.duckdb` table as of this writing holds the
**pre-filter, confirmed-truncated-for-some-codes** dataset (89,661 awards,
migrated from an earlier run, not re-fetched) — do not treat its dollar
figures as final until the amount filter lands.

## Ingestion architecture: one DuckDB file per source, merged after

`pipeline/ingest/epa.py`, `osha.py`, and `dod.py` each write to their own
DuckDB file (`data/epa.duckdb`, `data/osha.duckdb`, `data/dod.duckdb`) —
DuckDB allows only one writer per file at a time, and a shared file meant a
long-running DoD ingest (which can run for many minutes working through rate
limits) held a lock that blocked EPA or OSHA from even starting. `make
ingest-epa`, `make ingest-osha`, and `make ingest-dod` can now run
concurrently. `pipeline/ingest/merge.py` (`make merge`) attaches each source
file read-only and copies its table(s) into `data/facility_intel.duckdb`,
which is what `resolve`/`score`/`export` and the resolver read from. `make
ingest` runs all three sources then merge.

## Shared object model

All five agents read and write this. They never call each other directly.

```
ACCOUNT   account_id, legal_name, vertical, whale_score, est_TCV_ceiling,
          account_is_customer, facilities[], people[], deals[]

FACILITY  facility_id, account_id, address, lat/lon, sqft, employee_band,
          finishing_ops[], air_permit_class, voc_tons_yr,
          osha_dart_rate, ergonomic_recordables_3yr,
          dod_awards_here_ttm, open_finishing_reqs, installed_status,
          priority_rank, why_now, est_cells_capacity, est_facility_TCV

PART      part_id, facility_id, geometry_class, material, dimensions,
          annual_volume, current_process, manual_cycle_time,
          scrap_rate, rework_rate, fit_score, fit_blockers[]

PERSON    person_id, account_id, facility_id, title, role_class,
          engagement_state, coverage_gap

DEAL      deal_id, account_id, stage, cells_committed, current_TCV,
          ceiling_TCV, expansion_path[], capital_case_id, blockers[]
```

**`account_is_customer` (ACCOUNT, bool, default false)** and
**`installed_status` (FACILITY, `untouched | in_pipeline | installed`,
default `untouched`) are deliberately separate fields that do NOT inherit
from one another.** `account_is_customer` means the logo is a customer — it
says nothing about which of that account's plants have cells.
`installed_status` is what actually answers "does this specific plant have
cells," and starts `untouched` for every facility regardless of its
account's customer status, until a facility-level source specifically says
otherwise (none does yet — no facility is marked anything but `untouched`
today).

This was a real bug caught before it shipped, not a hypothetical: an
earlier version set facility `installed_status` by inheriting the account's
status. Marking the U.S. Air Force account as a customer would have silently
marked all three Air Logistics Complex facilities "installed" and zeroed
them out of the untouched-qualified-TCV count — deleting exactly the signal
this project exists to surface. The same bug would have quietly removed
Boeing and Raytheon plants from the headline expansion number. The demo
argument is "these logos are customers with mostly untouched plant
networks" — that argument requires the two fields to stay independent.
`account_is_customer` is seeded from the customer's own public website
(named/case-study customers) in `/pipeline/overrides/corporate_map.yaml`,
2026-08-01, so it is not confidential.

Reporting should always show both, per facility count: qualified facilities,
untouched-qualified facilities, and (once Agent 4 produces real per-cell
dollar figures — it's still mocked) an untouched TCV ceiling — never a
single collapsed "installed" number at the account level.

DuckDB tables: `epa_facilities`, `osha_establishments`, `dod_awards`
(per-source ingestion outputs, consolidated by `merge` into
`facility_intel.duckdb`), `facilities`, `accounts` (resolver outputs, not yet
built for the full universe — see Entity resolution below).

## Scoring model

Weights live in a YAML config, never hardcoded. They must be adjustable live in
the UI, because arguing with the weights in front of a customer is a feature.

| Signal | Weight | Rationale |
|---|---|---|
| VOC tonnage percentile within vertical | 0.30 | Strongest proxy for coating and finishing scale |
| DART rate percentile + 3yr trend | 0.25 | Ergonomic injury cost is the wedge |
| DoD awards at this place of performance, 24mo, log scaled | 0.20 | Backlog and reshoring signal |
| Employee count band | 0.15 | Plant size |
| Air permit class, Title V vs minor | 0.10 | Regulatory scale indicator |

The "why now" string is templated and deterministic, not LLM generated. Only
include clauses where underlying data exists.

### Facility qualification gate (pre-scoring)

Not every resolved facility should count toward `est_account_TCV_ceiling`.
Boeing resolves to 149 facilities, but most are labs, offices, and EPA
sub-registrations at a shared campus address — real records, wrong thing to
multiply by a per-cell TCV estimate. A facility qualifies only if it shows
real physical finishing/manufacturing activity:

- nonzero VOC tonnage (2017 or 2020), or
- an AIR MAJOR (Title V proxy) permit, or
- an OSHA record with `annual_average_employees >= MIN_QUALIFYING_EMPLOYEES`

`MIN_QUALIFYING_EMPLOYEES` is a config knob (`pipeline.resolve.anchors`,
default 100 — a 10-person site isn't absorbing a $600K robotic cell), not a
derived constant. Sensitivity at 10/50/100/250 employees, three anchor
accounts (2026-08-01 run):

| Account | Total facilities | 10+ | 50+ | 100+ | 250+ |
|---|---|---|---|---|---|
| Boeing | 149 | 33 | 33 | 33 | 31 |
| Oshkosh Corporation | 44 | 18 | 18 | 18 | 16 |
| Caterpillar | 71 | 16 | 15 | 15 | 15 |

The employee threshold barely moves these three accounts' qualified counts —
most of their qualifying facilities clear the bar via VOC tonnage or AIR
MAJOR permit, not the OSHA employee path. Worth re-checking once the full
universe is resolved, since smaller accounts may lean on the employee signal
more.

## Five-agent system

| Stage | Question | Status |
|---|---|---|
| 1. Facility Signal Engine | Which plant first | REAL, this is the build |
| 2. Part Fit Qualifier | Will the robot work here | Mocked |
| 3. Multithread Map | Who else needs to be in the deal | Mocked |
| 4. Capital Case Builder | How does this clear a committee | Mocked |
| 5. Expansion Engine | How does one cell become thirty | Mocked |

Agents 1 and 2 make the first deal possible. Agents 3 and 4 make it big.
Agent 5 is where the $20M lives.

Be explicit in the README about what is real and what is architecture. Do not
let the reader discover it.

### Note on Agent 4

The customer already runs a public self-serve value calculator. It is
single-site, single-application, and by its own disclaimer computes value BEFORE
subscription cost. So it produces gross value, not payback, not IRR.

Agent 4 extends that model rather than replacing it. Mirror their input field
names exactly: units per day, production days per year, operator count, fully
burdened labor rate, overtime hours, consumables per unit, consumables unit
cost, cycle time per unit, rework rate, injuries per year. Then add subscription
cost, cells per facility, and a network rollup. Show gross and net side by side.

## Known hard problem: entity resolution

Entity resolution is roughly 40 percent of total effort and the only part of
this that is not commodity work.

OSHA establishment names are free text entered by whoever filed the form. EPA
uses registry IDs nobody else has. USASpending uses recipient names matching
neither. Joining these to one physical plant, then to one parent company, is the
actual technical contribution here.

**Status: proven against the three anchor accounts** (`pipeline.resolve.anchors`,
run via `uv run python -m pipeline.resolve.report`) **and now run once against
the full universe** (`pipeline.resolve.universe`, writes real `facilities`/
`accounts` tables). `naics_universe.json`'s anchor accounts exist specifically
so the anchor claim is checkable, not asserted.

### Full-universe run: coarser than the anchor run, honestly

`pipeline.resolve.universe` reuses the anchor run's Stage 1 (facility) logic
unchanged, but Stage 2 (account grouping) has no equivalent to
`CANDIDATE_PATTERNS` — there's no hand-picked list of identity substrings for
"every company in the dataset." Accounts form by exact
`normalize_company_name` match instead, with `corporate_map.yaml` overrides
layered on top. Confirmed result (2026-08-01): 55,418 facility-candidate
records collapse into 41,281 account buckets, most of them tiny — real
companies fragment across sub-facility naming variance (`"CATERPILLAR INC"`
groups fine; `"OSHKOSH CORP - WEST PLT"` does not group with plain
`"Oshkosh Corporation"` without an alias). This is the documented, accepted
limitation, not a bug to chase down before the next session — it means
`account_is_customer` and `qualified_facilities` are undercounts for named
customers until more aliases get discovered and added, the same way
Solar Turbines / Progress Rail / Electro-Motive were found for Caterpillar.

**A real bug was caught and fixed on this run, not a hypothetical:** account
keys in `corporate_map.yaml` (`OSHKOSH_CORPORATION`, `RAYTHEON_TECHNOLOGIES`)
don't naturally match what `normalize_company_name` produces for those same
companies' own facility records (`"Oshkosh Corporation"` normalizes to
`"OSHKOSH"`, not `"OSHKOSH_CORPORATION"`) — so a company's *own* directly-named
plants landed in a separate, unflagged bucket from its aliased subsidiaries.
Fixed by auto-deriving a self-alias from every account's `canonical_name`
(`pipeline.resolve.universe._effective_alias_map`) rather than requiring one
hand-written per account. Verified by checking actual resolved output, not
assumed from reading the code — first run silently undercounted `account_is_customer`
for exactly this reason (6 of 14 seeded customers matched instead of 10).

Of the 14 accounts seeded `account_is_customer` in `corporate_map.yaml`, 10
matched at least one real source record as of this run: Boeing (101
facilities, 39 qualified), Caterpillar (51, 10), Oshkosh Corporation (42,
14), U.S. Air Force (25, 2 — the Air Logistics Complexes fragment into many
small EPA sub-registrations, most without their own VOC/Title V data, same
pattern as Boeing/Caterpillar), Federal Signal, Vactor, Janicki, Miller
Industries, Productive Plastics, Lawrence Brothers. Four (Raytheon
Technologies, Planet 9, Magee, Innovative Surface Works) have no matching
record under their exact name in any of our three sources — reported as a
gap, not silently dropped.

### Stage 1: facility resolution

EPA and OSHA records only (DoD attaches separately — see below). Address as
the primary signal, name similarity as a gate, never a standalone signal,
never overridden by distance alone:

- **Tier 1 (confidence 0.95):** exact usaddress-normalized address match.
- **Tier 2 (confidence 0.75):** geocoded within 750m AND name similarity
  >= 0.6 (token-sort ratio on legal-suffix-stripped names).
- **Tier 3: does not merge.** Within 750m but name similarity < 0.6 is
  written out as a `pending_review` flag on both records and excluded from
  scoring until a human confirms — never auto-merged. Over-merging corrupts
  downstream scores in a way under-merging doesn't: two unrelated companies
  sharing an industrial park is a real, common case at 750m, and distance
  alone is not a discriminator in either direction.

**750m, not a tighter radius** — large campuses (Boeing Everett, Caterpillar
sites, shipyards) routinely exceed a tighter threshold, and different sources
geocode to different points on the same site.

**Cluster diameter cap, separate from the pairwise 750m check.**
Single-linkage chaining (A-B close, B-C close => A and C merged) can walk a
cluster's true end-to-end span well past 750m even though every adjacent link
is individually legal. Tier 2 clusters whose true diameter (max pairwise
distance among ALL members, not just chain-adjacent ones) exceeds 1,500m are
queued for review rather than auto-resolved, since a wide diameter with no
address to anchor it means the *grouping itself* is in question.

**Tier 1 clusters get different treatment for the same diameter problem —
this is a coordinate-quality issue, not an identity question.** If two
records share one *exact* normalized address (so identity is already
settled) but their lat/lon disagree by more than ~100m, that's an FRS
geocoding error, not evidence they're different facilities. Confirmed real
and systemic, not a one-off: three separate Boeing addresses hit this in the
same run (Long Beach CA, Portland OR, Victorville CA) — one Long Beach
address had two FRS records 2,362m apart despite an identical address
string. The fix: merge on the address as normal, flag the disagreeing
coordinate(s) as suspect, and build the cluster's map-display point only
from the consensus (majority-agreeing) coordinates — excluding suspect
coordinates from the eventual map view rather than either queuing the merge
for review (it isn't an identity question) or trusting a coordinate that's
probably wrong. A tie (no majority, e.g. 2 records with 2 disagreeing
coordinates) flags both and leaves the facility without a map point rather
than guessing which one is right.

### Stage 2: corporate resolution

Normalize company names (strip legal suffixes, standardize whitespace/
punctuation), apply `/pipeline/overrides/corporate_map.yaml` for
subsidiaries/DBAs that don't share the parent's name, group into accounts.

**Company-identity matching, not substring search — this is general, not an
Oshkosh-specific patch.** Company names collide with place names constantly:
Oshkosh is a city in Wisconsin, Caterpillar is headquartered in Peoria, and a
plain substring search on "OSHKOSH" pulled in `Pioneer Metal Finishing Corp -
Oshkosh Div` and `Advanced Coatings Inc. - Oshkosh` — real companies, wrong
account. `pipeline.resolve.normalize.matches_as_company` instead requires the
candidate pattern to be a leading, word-boundary-respecting token of the
name (after stripping a leading registry-code prefix like `"WA317937871 -
"`, which FRS prepends to a large fraction of facility names, and a leading
article) — i.e. the record identifies AS that company, not merely mentions
it. Tradeoff: a DBA phrased as `"X: A Boeing Company"` won't match this way
and needs an explicit `CANDIDATE_PATTERNS` or `corporate_map.yaml` entry once
discovered (this is how `Millennium Space Systems` was found and added).
Precision over recall, consistent with the project's core principle.

`corporate_map.yaml` alias lookup is currently **exact match** on the
normalized name — a real limitation for the full-universe resolver (not the
anchor-scoped one, which uses a broader substring prefilter before override
lookup): `"PROGRESS RAIL SERVICES CORPORATION"` and `"PROGRESS RAIL
SERVICE"` normalize to two different strings and each needs its own entry.
Needs prefix/substring alias matching before it scales past the three
anchors.

### DoD attachment: account + city + state, not Stage 1

DoD does not participate in Stage 1 facility resolution — see the PoP
granularity finding above for why an award can't be pinned to one facility.
Awards attach post-resolution, at the (account, city, state) level, summed
and attributed to whichever resolved facilities of that account sit in that
city; shared across multiple facilities in the same city rather than
arbitrarily assigned to one.

### Geocoding

**US Census Bureau Geocoder** — free, no API key, no documented rate limit
(checked their FAQ directly), but also no SLA, so requests are self-paced
rather than treated as unlimited. The anchor-scoped run uses the
single-address endpoint (low volume — tens of records). The full-universe
run should switch to the **batch endpoint** (up to 10,000 addresses per file
in one call) instead of looping single-address calls thousands of times —
OSHA alone collapses to roughly 7,000 unique establishments needing
geocoding.

### Manual overrides

`/pipeline/overrides/corporate_map.yaml` — aliases only for subsidiaries/DBAs
actually observed in source data during a resolution run, never speculative
completions of a candidate search list. Candidates searched for but not
found are logged in the file as comments, not silently omitted.

Every match carries a confidence score and a `match_reason` string
explaining exactly which signal fired (e.g. "exact normalized address match
'700 15TH ST, AUBURN WA 98002'"); every `pending_review` entry states why it
wasn't auto-resolved. "Why did these two records get merged" always has a
concrete answer.

## Session hygiene

Sessions are cleared between phases. Commit before every clear. Anything
important that exists only in conversation is lost. If a decision matters beyond
the current session, it belongs in this file or in a code comment.

## Working style

Small, surgical changes. State assumptions before acting on them. Define
verifiable success criteria and actually run them before declaring done. Do not
add abstraction, config layers, or dependencies that were not asked for.
