"""Prints the anchor-account resolution report: run `uv run python -m pipeline.resolve.report`."""

from pipeline.resolve.anchors import CANDIDATE_PATTERNS, EXPECTED_BOEING_CITIES, run


def _sources_for_cluster(records, cluster) -> str:
    sources = sorted({records[i]["source"].upper() for i in cluster["member_idxs"]})
    return "+".join(sources)


def print_report(results: dict) -> None:
    for account_key in CANDIDATE_PATTERNS:
        data = results[account_key]
        records = data["records"]
        clusters = data["clusters"]
        pending = data["pending_review"]
        dod_attr = data["dod_attribution"]

        print(f"\n{'=' * 100}\n{account_key}  ({data['total_facilities']} total facilities, {data['qualified_facilities']} qualified for TCV, from {len(records)} source records)\n{'=' * 100}")
        sweep = data["qualification_sweep"]
        print("  qualified-facility count by employee threshold: " + ", ".join(f"{t}+ employees = {n}" for t, n in sweep.items()))

        by_city = sorted(clusters, key=lambda c: (records[c["member_idxs"][0]]["city"] or "").upper())
        for c in by_city:
            rep = records[c["member_idxs"][0]]
            sources = _sources_for_cluster(records, c)
            member_desc = "; ".join(f"{records[i]['source']}:{records[i]['name']}" for i in c["member_idxs"])
            qual = "QUALIFIED" if c["qualified"] else "not qualified"
            coord_note = ""
            if c.get("suspect_coordinate_idxs"):
                coord_note = f"  [SUSPECT COORDINATES: {len(c['suspect_coordinate_idxs'])} member(s) excluded from map view]"
            print(f"\n- {rep['city']}, {rep['state']}  [tier={c['tier']} conf={c['confidence']}]  sources={sources}  [{qual}]{coord_note}")
            print(f"    members: {member_desc}")
            print(f"    match_reason: {c['reason']}")
            print(f"    qualification: {c['qualification_reason']}")
            print(f"    map point: {c['map_lat']}, {c['map_lon']}")

        print(f"\n--- pending_review ({len(pending)}) ---")
        if not pending:
            print("  (none)")
        for p in pending:
            print(f"  {p['record_a']}  <->  {p['record_b']}")
            print(f"    {p['reason']}")

        print(f"\n--- DoD attribution (account+city+state level; PoP data has no street address) ---")
        if not dod_attr:
            print("  (no DoD awards matched this account)")
        for (city, state), info in sorted(dod_attr.items()):
            print(f"  {city}, {state}: ${info['total_amount']:,.0f} across {info['award_count']} awards -> {info['note']}")

        if account_key == "BOEING":
            print("\n--- expected-city sanity check (Everett, Renton, Charleston, St. Louis, Mesa) ---")
            resolved_cities = {(records[c["member_idxs"][0]]["city"] or "").upper() for c in clusters}
            for expected in EXPECTED_BOEING_CITIES:
                present = any(expected in rc for rc in resolved_cities)
                print(f"  {expected}: {'FOUND' if present else 'MISSING'}")


if __name__ == "__main__":
    results = run()
    print_report(results)
