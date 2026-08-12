"""One-shot data payload for the Apollo enrichment cache build (2026-08-02).

This is NOT a re-runnable ingestion module. It is the literal result of the
Apollo MCP calls made in that session -- free `organizations/search` lookups
(company name -> domain/website/linkedin, no credit cost) followed by paid
`organizations/bulk_enrich` calls (domain -> employee count + HQ, 1 credit
per match, 34 credits spent, all 34 matched) against the top 50 accounts by
untouched_tcv as of that export. See `build_enrichment_cache.py`, which
consumes this data and writes /data/enrichment/accounts.json.

Kept as a separate data module (not inlined in the builder) so the builder
script reads like the actual cache-check-then-fetch logic a re-run would
follow, without pretending this data was fetched by that logic just now.
"""

# account_id -> Apollo org data. Only accounts Apollo could confidently match
# to a real company with a usable primary_domain are here. Accounts searched
# but not resolved to a confident domain are listed in UNMATCHED below
# instead of guessed.
MATCHED = {
    "LOCKHEED_MARTIN": dict(domain="lockheedmartin.com", website_url="http://www.lockheedmartin.com", linkedin_url="http://www.linkedin.com/company/lockheed-martin", city="Bethesda", state="Maryland", employee_count=121000),
    "BOEING": dict(domain="boeing.com", website_url="http://www.boeing.com", linkedin_url="http://www.linkedin.com/company/boeing", city="Arlington", state="Virginia", employee_count=172000),
    "PRECOAT METALS": dict(domain="precoat.com", website_url="http://www.precoat.com", linkedin_url="http://www.linkedin.com/company/precoat-metals", city="St. Louis", state="Missouri", employee_count=2000),
    "CATERPILLAR": dict(domain="caterpillar.com", website_url="http://www.caterpillar.com", linkedin_url="http://www.linkedin.com/company/caterpillar-inc", city="Irving", state="Texas", employee_count=113000),
    "OSHKOSH_CORPORATION": dict(domain="oshkoshcorp.com", website_url="http://www.oshkoshcorp.com", linkedin_url="http://www.linkedin.com/company/oshkosh-corporation", city="Oshkosh", state="Wisconsin", employee_count=19000),
    "SPACE EXPLORATION TECHNOLOGIES": dict(domain="spacex.com", website_url="http://www.spacex.com", linkedin_url="http://www.linkedin.com/company/spacex", city="Hawthorne", state="California", employee_count=21000),
    "GREAT DANE": dict(domain="greatdane.com", website_url="http://www.greatdane.com", linkedin_url="http://www.linkedin.com/company/greatdane", city="Chicago", state="Illinois", employee_count=5500),
    "FOREST RIVER": dict(domain="forestriverinc.com", website_url="http://www.forestriverinc.com", linkedin_url="http://www.linkedin.com/company/forest-river-inc.", city="Elkhart", state="Indiana", employee_count=15000),
    "GULFSTREAM AEROSPACE": dict(domain="gulfstream.com", website_url="http://www.gulfstream.com", linkedin_url="http://www.linkedin.com/company/gulfstreamaero", city="Savannah", state="Georgia", employee_count=20000),
    "UTILITY TRAILER MANUFACTURING": dict(domain="utilitytrailer.com", website_url="http://www.utilitytrailer.com", linkedin_url="http://www.linkedin.com/company/utility-trailer-manufacturing-company", city="City of Industry", state="California", employee_count=3100),
    "EASTERN SHIPBUILDING": dict(domain="easternshipbuilding.com", website_url="http://www.easternshipbuilding.com", linkedin_url="http://www.linkedin.com/company/eastern-shipbuilding-group", city="Panama City", state="Florida", employee_count=1600),
    "WOODWARD": dict(domain="woodward.com", website_url="http://www.woodward.com", linkedin_url="http://www.linkedin.com/company/woodwardinc", city="Fort Collins", state="Colorado", employee_count=9300),
    "HONEYWELL INTERNATIONAL": dict(domain="honeywell.com", website_url="http://www.honeywell.com", linkedin_url="http://www.linkedin.com/company/honeywell", city="Charlotte", state="North Carolina", employee_count=102000),
    "NORTHROP GRUMMAN SYSTEMS": dict(domain="northropgrumman.com", website_url="http://www.northropgrumman.com", linkedin_url="http://www.linkedin.com/company/northrop-grumman-corporation", city="Falls Church", state="Virginia", employee_count=97000),
    "VALMONT": dict(domain="valmont.com", website_url="http://www.valmont.com", linkedin_url="http://www.linkedin.com/company/valmontindustriesinc", city="Omaha", state="Nebraska", employee_count=11000),
    "AGCO": dict(domain="agcocorp.com", website_url="http://www.agcocorp.com", linkedin_url="http://www.linkedin.com/company/agco-corporation", city="Duluth", state="Georgia", employee_count=24000),
    "SIMPSON STRONG TIE": dict(domain="strongtie.com", website_url="http://www.strongtie.com", linkedin_url="http://www.linkedin.com/company/simpson-strong-tie", city="Pleasanton", state="California", employee_count=5500),
    "BAE SYSTEMS": dict(domain="baesystems.com", website_url="http://www.baesystems.com", linkedin_url="http://www.linkedin.com/company/bae-systems", city=None, state="England", employee_count=107000),
    "AMSTED RAIL": dict(domain="amstedrail.com", website_url="http://www.amstedrail.com", linkedin_url="http://www.linkedin.com/company/amsted-rail-inc", city="Chicago", state="Illinois", employee_count=1200),
    "MORGAN OLSON": dict(domain="morganolson.com", website_url="http://www.morganolson.com", linkedin_url="http://www.linkedin.com/company/morganolson", city="Sturgis", state="Michigan", employee_count=690),
    "HAMILTON SUNDSTRAND": dict(domain="hamiltonsundstrand.com", website_url="http://www.hamiltonsundstrand.com", linkedin_url="http://www.linkedin.com/company/hamilton-sundstrand", city="Windsor Locks", state="Connecticut", employee_count=2000),
    "COBALT BOATS": dict(domain="cobaltboats.com", website_url="http://www.cobaltboats.com", linkedin_url="http://www.linkedin.com/company/cobalt-boats", city="Neodesha", state="Kansas", employee_count=620),
    "HANNA STEEL": dict(domain="hannasteel.com", website_url="http://www.hannasteel.com", linkedin_url="http://www.linkedin.com/company/hanna-steel-corporation", city="Hoover", state="Alabama", employee_count=300),
    "BOSTON WHALER": dict(domain="bostonwhaler.com", website_url="http://www.bostonwhaler.com", linkedin_url="http://www.linkedin.com/company/boston-whaler", city="Edgewater", state="Florida", employee_count=590),
    "DUNCAN AVIATION": dict(domain="duncanaviation.aero", website_url="http://www.duncanaviation.aero", linkedin_url="http://www.linkedin.com/company/duncan-aviation", city="Lincoln", state="Nebraska", employee_count=2200),
    "SPRAYTEK": dict(domain="spraytekinc.com", website_url="http://www.spraytekinc.com", linkedin_url="http://www.linkedin.com/company/spraytek-inc.", city="Ferndale", state="Michigan", employee_count=27),
    "JOBY AVIATION": dict(domain="jobyaviation.com", website_url="http://www.jobyaviation.com", linkedin_url="http://www.linkedin.com/company/jobyaviation", city="Santa Cruz", state="California", employee_count=2100),
    "PROFILE FINISHING SYSTEMS": dict(domain="profilefinishing.com", website_url="http://www.profilefinishing.com", linkedin_url="http://www.linkedin.com/company/profile-finishing-systems-inc", city="Kaukauna", state="Wisconsin", employee_count=30),
    "THOR MOTOR COACH": dict(domain="thormotorcoach.com", website_url="http://www.thormotorcoach.com", linkedin_url="http://www.linkedin.com/company/thor-motor-coach", city="Elkhart", state="Indiana", employee_count=2000),
    # Enrich matched a mis-associated subsidiary (Formax UK) for this domain --
    # employee count and HQ are still Hexcel's real figures (Stamford, CT is
    # Hexcel's actual HQ), but website/linkedin are taken from the earlier
    # free-search match instead of the enrich response, which is more
    # obviously correct for those two fields.
    "HEXCEL": dict(domain="hexcel.com", website_url="http://www.hexcel.com", linkedin_url="http://www.linkedin.com/company/hexcel-corporation", city="Stamford", state="Connecticut", employee_count=5900),
    "CANAM STEEL": dict(domain="cscsteelusa.com", website_url="http://www.cscsteelusa.com", linkedin_url="http://www.linkedin.com/company/canam-steel-corporation", city="Point of Rocks", state="Maryland", employee_count=510),
    "CARFAIR COMPOSITES USA": dict(domain="carfaircomposites.com", website_url="http://www.carfaircomposites.com", linkedin_url="http://www.linkedin.com/company/carfair-composites", city="Winnipeg", state="Manitoba", employee_count=130),
    "BLUESCOPE COATED PRODUCTS": dict(domain="bluescopecoatedproducts.com", website_url="http://www.bluescopecoatedproducts.com", linkedin_url="http://www.linkedin.com/company/bluescope-coated-products", city="Middletown", state="Ohio", employee_count=510),
    # Enrich response for this domain returned Mecalux's Spain HQ, not
    # Interlake Mecalux's US entity -- kept because employee count/domain
    # still describe the joint venture at a global level, but HQ is flagged
    # low-confidence rather than trusted at face value.
    "INTERLAKE MECALUX": dict(domain="interlakemecalux.com", website_url="http://www.interlakemecalux.com", linkedin_url="http://www.linkedin.com/company/interlake-mecalux-warehouse-solutions", city=None, state=None, employee_count=4600, hq_low_confidence=True),
}

# account_id -> reason Apollo could not confidently resolve this account.
# Logged, not silently skipped, per explicit instruction.
UNMATCHED = {
    "RAYTHEON_TECHNOLOGIES": "Apollo has an org record for 'RTX' (id 5f4a5821e8ca1c00c7ee9739) but no domain populated on it -- cannot enrich without a domain, and guessing rtx.com was not attempted.",
    "US_NAVY": "Apollo's global company database is commercial/B2B; the only close name match was 'U.S. Navy Reserve' (a recruiting-office org, not the service branch) with no domain. Federal military branches are not a good fit for this data source.",
    "PACTIV": "Zero results for 'Pactiv LLC' in Apollo's company search.",
    "WASTEQUIP MANUFACTURING": "Zero results for 'Wastequip Manufacturing Company' in Apollo's company search.",
    "PARKER HANNIFIN": "Closest match was 'Parker Hannifin International Corp' (id 66deb5214c79590001c00885), a subsidiary entity with no domain populated -- not the same account we'd be attaching a link to, so left unmatched rather than assumed equivalent.",
    "CNH INDUSTRIAL AMERICA": "Closest match was 'New Holland CE - CNH - Fiat Industrial' (id 66a1af72a2b21c019bf4b62d), no domain populated, and the name doesn't clearly correspond to the same legal entity as our resolved account.",
    "ALTEC": "Apollo record found (id 6172b0bc97396700a4ab0e74) but no domain populated.",
    "LIPPERT COMPONENTS": "Apollo record found but no domain populated, and its LinkedIn ('hehr-international') suggests a different sub-brand than the resolved account name.",
    "LEARJET": "Only match was 'Bombardier Aerospace Learjet Inc' with no domain populated -- a real subsidiary name but no usable domain.",
    "CNH AMERICA": "Only match was 'New Holland CE - CNH - Fiat Industrial' with no domain populated.",
    "FLEXCON": "Only match was 'Flexcon Vietnam Company Ltd', a different, unrelated entity -- not accepted as a match.",
    "PREGIS INNOVATIVE PACKAGING": "Apollo record found but no domain populated.",
    "NEW MILLENNIUM BUILDING SYSTEMS": "Zero results in Apollo's company search.",
    "SONOCO PRODUCTS": "Zero results in Apollo's company search.",
    "BRP US": "Zero results in Apollo's company search.",
    "WABASH NATIONAL": "Only match was 'Wabash National Trailer Centers' (a regional dealer/service subsidiary, domain wabashnationalsatx.com), not confidently the same account as the OEM parent -- excluded rather than assumed equivalent.",
}
