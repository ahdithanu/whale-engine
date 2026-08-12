"""Company URL enrichment for the top accounts by untouched qualified TCV.

Reads the top N accounts from the export, matches each to an Apollo org via
`enrich_accounts_data.MATCHED` (account_id -> domain/website/linkedin/HQ/
employee_count, gathered via the Apollo MCP connector's free company search
+ paid bulk-enrich endpoints -- see that module's docstring), and writes
/data/enrichment/accounts.json.

Cache-checked, not re-fetched: this script does NOT call Apollo itself (the
MCP connector isn't reachable from a plain script run) -- it's the second
half of a two-step process. The first step (an interactive Apollo MCP
session) already spent the credits and recorded results in
enrich_accounts_data.py. Re-running THIS script is free and idempotent; it
only re-derives the cache file from already-fetched data. Matching key is
canonical_name from corporate_map.yaml where the account has one, else the
resolved legal_name -- consistent with how every other account-identity
decision in this pipeline is made.
"""

import json

import yaml

from pipeline.config import DATA_DIR, REPO_ROOT
from pipeline.enrich_accounts_data import MATCHED, UNMATCHED

TOP_N = 50
WEB_DATA_DIR = REPO_ROOT / "web" / "public" / "data"
ENRICHMENT_DIR = DATA_DIR / "enrichment"
CORPORATE_MAP_PATH = REPO_ROOT / "pipeline" / "overrides" / "corporate_map.yaml"


def build() -> None:
    accounts = json.loads((WEB_DATA_DIR / "accounts.json").read_text())
    corporate_map = yaml.safe_load(CORPORATE_MAP_PATH.read_text())
    cm_accounts = corporate_map.get("accounts", {})

    top = sorted(accounts, key=lambda a: a["untouched_tcv"], reverse=True)[:TOP_N]

    ENRICHMENT_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = ENRICHMENT_DIR / "accounts.json"
    cache = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text())
        print(f"[enrich] loaded existing cache: {len(cache)} accounts")

    matched, unmatched, cached_hits = 0, 0, 0
    for a in top:
        account_id = a["account_id"]
        canonical = cm_accounts.get(account_id, {}).get("canonical_name")
        search_name = canonical if canonical else a["legal_name"]

        if account_id in cache:
            cached_hits += 1
            continue

        if account_id in MATCHED:
            d = MATCHED[account_id]
            cache[account_id] = {
                "matched": True,
                "search_name": search_name,
                "primary_domain": d["domain"],
                "website_url": d["website_url"],
                "linkedin_url": d["linkedin_url"],
                "hq_city": d.get("city"),
                "hq_state": d.get("state"),
                "apollo_employee_count": d.get("employee_count"),
                "hq_low_confidence": d.get("hq_low_confidence", False),
            }
            matched += 1
        else:
            reason = UNMATCHED.get(account_id, "Not attempted in this enrichment pass.")
            cache[account_id] = {
                "matched": False,
                "search_name": search_name,
                "reason": reason,
            }
            unmatched += 1
            print(f"[enrich] UNMATCHED {account_id!r} ({search_name!r}): {reason}")

    cache_path.write_text(json.dumps(cache, indent=2))
    print(f"[enrich] wrote {cache_path}: {matched} newly matched, {unmatched} newly unmatched, "
          f"{cached_hits} already cached, {len(cache)} total")


if __name__ == "__main__":
    build()
