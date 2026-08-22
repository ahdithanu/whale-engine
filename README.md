# Whale Engine

Facility-level account intelligence for a robotic surface finishing company:
for a given manufacturer, which specific plant to walk into first, and how
much finishing work is likely sitting inside it.

## The insight

The addressable universe for robotic surface finishing is a few hundred US
manufacturers. This is not a lead generation problem — everyone in this
market already knows the company names (Boeing, Lockheed, Caterpillar,
Oshkosh). The problem is that a $20M account is not one purchase. It is one
logo, 8 to 15 plants, 2 to 4 cells per plant, sequenced over 3 to 5 years. The
revenue is created by the expansion, not the first sale — which means the
real question is not "who buys robots" but "which specific plant inside which
specific company do I walk into first, and what do I say when I get there."

Public environmental, safety, and federal contracting data answers that
question at the facility level, and almost nobody in GTM uses it. A plant
that emits a lot of coating VOC, has a bad and worsening injury rate, and
holds a federal contract at that location is showing three independent,
verifiable signs of heavy manual finishing labor — before anyone picks up a
phone.

## Data sources

| Source | Provides | Notes |
|---|---|---|
| [EPA Facility Registry Service](https://ordsext.epa.gov/FLA/www3/state_files/national_combined.zip) | Facility identity: name, address, lat/lon, NAICS code, air permit class | Bulk national file, not the REST API — see CLAUDE.md for why |
| [EPA National Emissions Inventory](https://gaftp.epa.gov/air/nei/nei_facility_summaries/) | Facility-level VOC and particulate (PM10/PM2.5) tonnage, 2017 and 2020 | Primary proxy for coating/finishing operation scale; two years to avoid the COVID-distorted 2020 figure standing alone |
| [OSHA Injury Tracking Application](https://www.osha.gov/Establishment-Specific-Injury-and-Illness-Data) | Establishment-level DART (Days Away, Restricted, Transferred) injury rate and headcount | Annual bulk CSV; names are free-text and messy, which is most of the entity-resolution problem below |
| [USASpending](https://api.usaspending.gov/api/v2/search/spending_by_award/) | DoD prime contract awards by place of performance, trailing 24 months | Public, unauthenticated API; place of performance is city/state-precise, not street-address-precise — see below |

Each source answers a different question: EPA says how much finishing
volume a plant is likely running, OSHA says how much of that volume is
still manual and expensive, USASpending says how much backlog and
reshoring pressure is sitting behind it. No single source is sufficient —
the value is in joining them to one physical plant, which is the hard part.

## Entity resolution

This is roughly 40 percent of total effort and the only part of this that is
not commodity work. Three sources, three different identity schemes: EPA
uses a registry ID nobody else has, OSHA establishment names are free text
typed by whoever filed the form, USASpending recipient names match neither.
Joining these to one physical plant, then to one parent company, is the
actual technical contribution here.

**Facility resolution** matches on normalized street address as the primary
signal, with geocoded proximity and name similarity as fallback tiers —
never distance alone, since two unrelated companies sharing an industrial
park is a common, real case. Three tiers, decreasing confidence: exact
address match, geocoded-proximity-plus-name-similarity match, and a
"does not merge" tier that gets queued for human review rather than
guessed. A separate cluster-diameter check catches the case where a chain
of individually-legal proximity matches walks a group's true span past a
sane radius.

**Corporate resolution** groups facilities into parent accounts by
normalized company name, with a hand-curated override file
(`pipeline/overrides/corporate_map.yaml`) for subsidiaries and DBAs that
don't share the parent's name — Solar Turbines and Progress Rail under
Caterpillar, Collins Aerospace and Pratt & Whitney under Raytheon
Technologies. Company-identity matching requires the candidate name to be a
leading, word-boundary token of the record — not a substring search — because
place names collide with company names constantly (Oshkosh is a city in
Wisconsin; a substring search on "Oshkosh" pulls in unrelated companies
physically located there).

The resolver is proven against three anchor accounts named by the customer
as their own real customers — Boeing, Oshkosh Corporation, Caterpillar — then
run once across the full ~50,000-facility candidate universe. Every merge
carries a confidence score and a stated reason; nothing is silently
auto-merged past the address-and-name-similarity gate.

## Scoring model

A 0-100 composite score per resolved facility from six weighted signals.
Weights live in a YAML config, not hardcoded, so they are adjustable live in
the dashboard — arguing with the weights in front of the customer is a
feature, not a bug to route around.

| Signal | Weight | Rationale |
|---|---|---|
| VOC tonnage percentile within vertical | 0.22 | Strongest proxy for coating and finishing scale |
| VOC tonnage trend (2017 to 2020) | 0.08 | Growing emissions despite the COVID demand dip is evidence of an expanding finishing operation, not noise |
| DART rate percentile and 3-year trend | 0.25 | Ergonomic injury cost is the wedge — a worsening rate is a stronger argument than a high-but-improving one |
| DoD awards at this location, 24 months, log-scaled | 0.20 | Backlog and reshoring signal |
| Employee count band | 0.15 | Plant size |
| Air permit class (Title V major vs. minor) | 0.10 | Regulatory scale indicator |

Percentiles rank within the facility's own vertical, not globally — a
400-ton VOC facility means something different in aerospace than in metal
fabrication. DoD dollars rank globally, since contracting volume is not a
function of physical plant scale the way VOC and DART are. A missing signal
always contributes zero to its weighted component; it is never excluded and
the remaining weights renormalized, since that would quietly reward a
facility for having less data.

TCV (total contract value ceiling) is derived from estimated finishing
headcount, itself derived from real OSHA employee counts where available and
a calibrated fallback chain (VOC tonnage, then PM10 tonnage, then vertical
median) where not. There is no floor under this: a facility whose estimated
headcount doesn't clear one cell's worth of labor shows $0 TCV rather than a
manufactured minimum. Every dollar figure in the dashboard traces to a real
signal or is explicitly flagged as derived.

## What is real and what is architecture

| Stage | Question | Status |
|---|---|---|
| 1. Facility Signal Engine | Which plant first | **Real.** Bulk ingestion, entity resolution, and the scoring model above are a working pipeline running against live EPA/OSHA/USASpending data, with a dashboard reading its output. |
| 2. Part Fit Qualifier | Will the robot work here | Architecture only. No part-level data source is wired up. |
| 3. Multithread Map | Who else needs to be in the deal | Architecture only. No person/contact data source is wired up. |
| 4. Capital Case Builder | How does this clear a committee | Architecture only. Designed to extend the customer's own public ROI calculator, not replace it — not implemented. |
| 5. Expansion Engine | How does one cell become thirty | **Real.** A pure derivation over Agent 1's own facility_score — no new data source. Ranks an account's untouched, qualified plants into a suggested rollout sequence, shown as the EXPANSION PATH panel on any account with more than one such plant. Not yet informed by real installed-cell outcome data, since no facility in this dataset has ever been marked installed. |

Agent 1 is the full ingestion/resolution/scoring pipeline and the dashboard.
Agent 5 is a real, working derivation on top of Agent 1's own output — no
new data source, so it shipped without waiting on one. Agents 2 through 4
exist as a defined object model and a stated design intent, not as working
code: they need a data source Agent 1 doesn't have (part specs, org
contacts, or user-entered financial inputs) that hasn't been wired up yet.
This is stated explicitly here so it is not discovered later — see
CLAUDE.md for the full object model and scoring-model detail.

## The five-stage system and the $20M account

Agents 1 and 2 make the first deal possible: which plant, and does the part
mix actually fit a robotic cell. Agents 3 and 4 make it big: who else in the
org needs to be in the room, and how does a second and third cell clear a
capital committee once the first one is running. Agent 5 is where the $20M
actually lives — one cell in one plant is a small deal; the same logo's
other 7 to 14 plants, sequenced over several years, is the account. Agent 1
is the only stage built because it is the one that makes every later stage
possible: without a defensible answer to "which plant first," there is
nothing to expand from.

## Running it

```
make setup             # uv sync + npm install
make ingest             # epa/osha/dod (each to its own DuckDB file) + merge
make resolve             # full-universe entity resolution -> facilities/accounts tables
uv run python -m pipeline.resolve.report   # anchor-scoped resolver report (Boeing/Oshkosh/Caterpillar)
make score               # scoring model -> facility scores, why-now strings, account rollups
make export               # writes web/public/data/*.json for the dashboard
make dev                   # Next.js dashboard at localhost:3000
```

The dashboard's headline number is the "whale tier": accounts whose own
untouched qualified TCV ceiling already clears $20M on its own, before any
expansion — the number this project is named after. A filter toggle switches
the account table between whale tier and the full resolved universe.

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
  coverage is likely undercounted" section for the full explanation.
- **Full-universe entity resolution is coarser than the proven anchor run.**
  Accounts form by exact normalized-name match plus hand-curated aliases in
  `pipeline/overrides/corporate_map.yaml`; real companies with sub-facility
  naming variance (`"CATERPILLAR INC"` vs `"OSHKOSH CORP - WEST PLT"`)
  fragment into separate accounts until an alias is added. Lockheed Martin
  was found fragmented into three accounts this way and merged by hand
  2026-08-02 — a known, accepted limitation of the current alias-matching
  approach, not a bug to chase down blind.
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

Full working context and decisions live in [`CLAUDE.md`](CLAUDE.md).
