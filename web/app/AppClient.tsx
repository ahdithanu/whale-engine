"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Account,
  Facility,
  PendingReview,
  SIGNAL_LABELS,
  SIGNAL_SOURCES,
  SignalKey,
  Weights,
  DEFAULT_WEIGHTS,
  isGovernmentEndUser,
} from "@/lib/types";
import { rollupAccounts, scoreFacility, nSignalsPresent, money, AccountRollup } from "@/lib/scoring";
import { resolveFacilityDisplayName, resolveAccountDisplayName } from "@/lib/displayName";

const C = {
  bg: "#0E1113",
  panel: "#171B1F",
  panel2: "#12161A",
  border: "#232A2F",
  rowBorder: "#1B2126",
  text: "#E6EAEC",
  text2: "#DCE3E7",
  muted: "#9DAAB2",
  muted2: "#B7C2C8",
  muted3: "#8D9AA2",
  faint: "#7C8A93",
  faint2: "#5F6D76",
  accent: "#F5A623",
  accentDim: "#6B4E17",
  installed: "#39464E",
  pipeline: "#6C7C86",
  chipBorder: "#2A3238",
};

const MONO = "var(--font-plex-mono), monospace";
const SANS = "var(--font-plex-sans), Helvetica, sans-serif";

type View = "accounts" | "account" | "facility" | "map" | "review" | "brief";

// The mission is $20M+ enterprise accounts (see CLAUDE.md: "A $20M account is
// not one purchase"), not the long tail -- so "whale tier" is the accounts
// whose OWN untouched qualified TCV ceiling already clears that bar on its
// own, before any expansion. Not a config knob yet: this is the one number
// the whole project is named after, not a UI preference to make adjustable.
const WHALE_THRESHOLD = 20_000_000;

export default function AppClient({
  initialAccounts,
  initialFacilities,
}: {
  initialAccounts: Account[];
  initialFacilities: Facility[];
}) {
  // Seeded from server-fetched props (see app/page.tsx), not a client-only
  // useEffect fetch -- that fetch was why the site was dead on arrival when
  // shared: curl (and any link-preview crawler) got only "LOADING WHALE
  // ENGINE..." because accounts/facilities were still null at the time the
  // server rendered the page. Seeding real data as the initial state means
  // the server's first render already paints the real headline numbers,
  // and the client picks up from there without a network round trip.
  const [accounts] = useState<Account[]>(initialAccounts);
  const [facilities] = useState<Facility[]>(initialFacilities);
  const [pendingReviews, setPendingReviews] = useState<PendingReview[] | null>(null);

  const [view, setView] = useState<View>("accounts");
  const [acctId, setAcctId] = useState<string | null>(null);
  const [facId, setFacId] = useState<string | null>(null);
  const [showOrient, setShowOrient] = useState(true);
  const [weightsOpen, setWeightsOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<"all" | "untouched" | "installed">("all");
  const [tierFilter, setTierFilter] = useState<"whale" | "all">("whale");
  const [isMobile, setIsMobile] = useState(false);
  const [zoom, setZoom] = useState(2);
  const [weights, setWeights] = useState<Weights>(DEFAULT_WEIGHTS);
  // 35, not the original 15 -- 15 barely gated anything (2,382 of 8,127
  // exported facilities cleared qualified_for_tcv AND score>=15, a 29%
  // pass rate against the addressable-universe thesis this tool exists to
  // prove). Checked the real distribution, not guessed: at 35, 577
  // facilities across 6 whale accounts clear the bar ($2.85B total
  // untouched TCV) -- inside the 300-800 target band, confirmed with the
  // customer before changing this default (2026-08-11).
  const [threshold] = useState(35);
  const [churn, setChurn] = useState(0);
  const [prevOrder, setPrevOrder] = useState<string[]>([]);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth < 760);
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (view === "review" && pendingReviews === null) {
      fetch("/api/pending-review")
        .then((r) => r.json())
        .then((d) => setPendingReviews(d.reviews));
    }
  }, [view, pendingReviews]);

  const rollups = useMemo(() => {
    return rollupAccounts(accounts, facilities, weights, threshold);
  }, [accounts, facilities, weights, threshold]);

  function setWeight(k: SignalKey, v: number) {
    const before = rollups.map((r) => r.account.account_id);
    setPrevOrder(before);
    setWeights((w) => ({ ...w, [k]: v }));
  }

  useEffect(() => {
    if (prevOrder.length === 0) return;
    const after = rollups.map((r) => r.account.account_id);
    let moved = 0;
    after.forEach((id, i) => {
      if (prevOrder[i] !== id) moved++;
    });
    setChurn(moved);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rollups]);

  // Government end users (U.S. Navy, U.S. Air Force, etc.) are excluded from
  // the whale tier -- see isGovernmentEndUser for why a federal service
  // branch shouldn't count alongside Lockheed/Boeing in this headline.
  const whaleRollups = rollups.filter((r) => r.untouchedTcv >= WHALE_THRESHOLD && !isGovernmentEndUser(r.account.legal_name));
  const whaleTcv = whaleRollups.reduce((s, r) => s + r.untouchedTcv, 0);
  const whaleUntCount = whaleRollups.reduce((s, r) => s + r.untouchedQualifiedCount, 0);
  const custTcv = rollups.filter((r) => r.account.account_is_customer).reduce((s, r) => s + r.untouchedTcv, 0);
  const qualCount = rollups.reduce((s, r) => s + r.qualifiedCount, 0);
  const maxTcv = rollups[0]?.untouchedTcv || 1;
  const displayedRollups = tierFilter === "whale" ? whaleRollups : rollups;

  const selectedRollup = acctId ? rollups.find((r) => r.account.account_id === acctId) ?? null : null;
  const selectedFacility = facId ? facilities.find((f) => f.facility_id === facId) ?? null : null;
  const selectedFacilityAccount = selectedFacility ? accounts.find((a) => a.account_id === selectedFacility.account_id) ?? null : null;

  const activeNav = view === "account" || view === "facility" ? "accounts" : view;

  return (
    <div style={{ background: C.bg, color: C.text, fontFamily: SANS, minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      {/* HEADER */}
      <div style={{ position: "sticky", top: 0, zIndex: 20, background: C.bg, borderBottom: `1px solid ${C.border}` }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, padding: "10px 16px", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
            <div style={{ fontFamily: MONO, fontWeight: 600, fontSize: 14, letterSpacing: "0.14em" }}>WHALE ENGINE</div>
            <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.12em", color: C.faint, whiteSpace: "nowrap" }}>FACILITY SIGNAL ENGINE · LIVE</div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
            {([["ACCOUNTS", "accounts"], ["MAP", "map"], ["REVIEW QUEUE", "review"], ["BRIEF", "brief"]] as [string, View][]).map(([label, v]) => {
              const on = activeNav === v;
              return (
                <div
                  key={v}
                  onClick={() => {
                    setView(v);
                    setAcctId(null);
                    setStatusFilter("all");
                  }}
                  style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.12em", padding: "7px 11px", cursor: "pointer", whiteSpace: "nowrap", borderBottom: `2px solid ${on ? C.accent : "transparent"}`, color: on ? C.text : C.faint }}
                >
                  {label}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* ORIENTATION */}
      {showOrient && (
        <div style={{ background: C.panel, borderBottom: `1px solid ${C.border}`, padding: "18px 16px 20px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 24, alignItems: "flex-start" }}>
            <div style={{ maxWidth: 900, display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.14em", color: C.faint }}>ORIENTATION</div>
              <div style={{ fontSize: 14, lineHeight: 1.5, color: C.text }}>
                Whale Engine ranks individual manufacturing plants by how much robotic surface finishing work is likely sitting inside them, then rolls those plants up to the parent company that owns them.
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.5, color: C.muted }}>
                <span style={{ color: C.faint, fontFamily: MONO, fontSize: 11, letterSpacing: "0.08em" }}>SOURCES&nbsp;&nbsp;</span>
                EPA Facility Registry Service and National Emissions Inventory · OSHA Injury Tracking Application · USASpending federal contract awards.
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.5, color: C.muted }}>
                <span style={{ color: C.faint, fontFamily: MONO, fontSize: 11, letterSpacing: "0.08em" }}>UNTOUCHED QUALIFIED TCV&nbsp;&nbsp;</span>
                Contract value in plants that clear the signal threshold and have no cell installed and no open pipeline — most of it inside logos we have already landed.
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.5, color: C.muted }}>
                <span style={{ color: C.faint, fontFamily: MONO, fontSize: 11, letterSpacing: "0.08em" }}>STATUS&nbsp;&nbsp;</span>
                The Facility Signal Engine is live on real data. Full picture in the BRIEF tab.
              </div>
            </div>
            <div onClick={() => setShowOrient(false)} style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.12em", color: C.faint, border: `1px solid ${C.chipBorder}`, padding: "6px 10px", cursor: "pointer", whiteSpace: "nowrap" }}>
              DISMISS
            </div>
          </div>
        </div>
      )}

      {/* KPI ROW */}
      <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 2.1fr 1fr", borderBottom: `1px solid ${C.border}` }}>
        <div style={{ padding: 16, borderRight: `1px solid ${C.border}` }}>
          <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.14em", color: C.faint }}>WHALE TIER UNTOUCHED QUALIFIED TCV</div>
          <div style={{ fontFamily: MONO, fontSize: 26, fontWeight: 500, color: C.text, marginTop: 8, letterSpacing: "-0.01em" }}>{money(whaleTcv)}</div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.faint2, marginTop: 4 }}>{whaleRollups.length} ACCOUNTS ≥ $20M CEILING · {whaleUntCount} UNTOUCHED QUALIFIED PLANTS</div>
        </div>
        <div style={{ padding: 16, borderRight: `1px solid ${C.border}`, background: C.panel2 }}>
          <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.14em", color: C.accent }}>UNTOUCHED TCV INSIDE EXISTING CUSTOMERS</div>
          <div style={{ fontFamily: MONO, fontSize: 56, lineHeight: 1.05, fontWeight: 600, color: C.accent, marginTop: 6, letterSpacing: "-0.02em" }}>{money(custTcv)}</div>
          <div style={{ fontSize: 12, color: C.muted, marginTop: 6, maxWidth: 520 }}>
            Already-landed logos. No new procurement relationship required.
          </div>
        </div>
        <div style={{ padding: 16 }}>
          <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.14em", color: C.faint }}>QUALIFIED FACILITIES</div>
          <div style={{ fontFamily: MONO, fontSize: 26, fontWeight: 500, color: C.text, marginTop: 8 }}>{qualCount}</div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.faint2, marginTop: 4 }}>OF {facilities.length} RESOLVED PLANTS · THRESHOLD ≥ {threshold}</div>
        </div>
      </div>

      {/* PROVENANCE — the untouched/installed/pipeline claim above is the single
          easiest thing in this app to disprove, so it's named here, under the
          two TCV cards it feeds, above the fold, not buried in a footnote. */}
      <div style={{ padding: "10px 16px", borderBottom: `1px solid ${C.border}`, background: C.bg }}>
        <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.1em", color: C.faint2, lineHeight: 1.6 }}>
          PROVENANCE&nbsp;&nbsp;Customer status is inferred from the public logo wall. Installed and pipeline flags require a CRM join and are currently unpopulated, so every qualified plant defaults to untouched.
        </div>
      </div>

      {/* MAIN */}
      <div style={{ flex: 1 }}>
        {view === "accounts" && (
          <AccountsView
            rollups={displayedRollups}
            totalAccounts={rollups.length}
            isMobile={isMobile}
            maxTcv={maxTcv}
            tierFilter={tierFilter}
            setTierFilter={setTierFilter}
            onOpen={(id) => {
              setAcctId(id);
              setView("account");
              setStatusFilter("all");
            }}
          />
        )}

        {view === "account" && selectedRollup && (
          <AccountDetailView
            rollup={selectedRollup}
            weights={weights}
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
            threshold={threshold}
            onBack={() => { setView("accounts"); setAcctId(null); }}
            onOpenFacility={(id) => { setFacId(id); setView("facility"); }}
            onMap={() => { setView("map"); setZoom(2); }}
          />
        )}

        {view === "facility" && selectedFacility && selectedFacilityAccount && (
          <FacilityDetailView
            facility={selectedFacility}
            account={selectedFacilityAccount}
            weights={weights}
            threshold={threshold}
            onBack={() => setView(acctId ? "account" : "accounts")}
          />
        )}

        {view === "map" && (
          <MapView
            facilities={acctId ? facilities.filter((f) => f.account_id === acctId) : facilities}
            accounts={accounts}
            title={acctId ? `${resolveAccountDisplayName(accounts.find((a) => a.account_id === acctId)?.legal_name ?? "", accounts.find((a) => a.account_id === acctId)?.vertical_name ?? null)} NETWORK` : "ALL RESOLVED PLANTS"}
            zoom={zoom}
            setZoom={setZoom}
            onOpenFacility={(id) => { setFacId(id); setView("facility"); }}
          />
        )}

        {view === "review" && <ReviewQueueView reviews={pendingReviews} />}

        {view === "brief" && <BriefView />}
      </div>

      {/* SENSITIVITY ANALYSIS (formerly "MODEL WEIGHTS") — moved below the
          account list and collapsed by default; RANK MOVED only renders once
          a weight has actually been moved, not as always-visible chrome. */}
      <div style={{ borderTop: `1px solid ${C.border}`, background: C.panel2 }}>
        <div onClick={() => setWeightsOpen((s) => !s)} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "9px 16px", cursor: "pointer", gap: 16 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
            <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.14em", color: C.accent, whiteSpace: "nowrap" }}>SENSITIVITY ANALYSIS</div>
            <div style={{ fontSize: 11.5, color: C.faint }}>Argue with the model. Move a weight, the ranking re-sorts live.</div>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.12em", color: C.muted, whiteSpace: "nowrap" }}>{weightsOpen ? "COLLAPSE −" : "EXPAND +"}</div>
        </div>
        {weightsOpen && (
          <div style={{ padding: "4px 16px 18px" }}>
            <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr 1fr" : "repeat(5,1fr)", gap: "14px 22px" }}>
              {(Object.keys(SIGNAL_LABELS) as SignalKey[]).map((k) => {
                const wSum = (Object.keys(SIGNAL_LABELS) as SignalKey[]).reduce((s, kk) => s + weights[kk], 0) || 1;
                return (
                  <div key={k}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                      <div style={{ fontSize: 11.5, color: C.muted2, whiteSpace: "nowrap" }}>{SIGNAL_LABELS[k]}</div>
                      <div style={{ fontFamily: MONO, fontSize: 12, color: C.accent }}>{Math.round((weights[k] / wSum) * 100)}%</div>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={50}
                      step={1}
                      value={weights[k]}
                      onChange={(e) => setWeight(k, parseInt(e.target.value, 10))}
                      style={{ width: "100%", marginTop: 8, display: "block" }}
                    />
                    <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.faint2, marginTop: 5 }}>{SIGNAL_SOURCES[k]}</div>
                  </div>
                );
              })}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 16, flexWrap: "wrap" }}>
              <div onClick={() => { setWeights(DEFAULT_WEIGHTS); setChurn(0); }} style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.12em", border: `1px solid ${C.chipBorder}`, padding: "6px 10px", cursor: "pointer", color: C.muted }}>
                RESET
              </div>
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.faint }}>
                QUALIFY THRESHOLD ≥ {threshold} · {qualCount} FACILITIES CLEAR IT{churn !== 0 ? ` · RANK MOVED ${churn} POSITIONS` : ""}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* FOOTER */}
      <div style={{ borderTop: `1px solid ${C.border}`, padding: "14px 16px 20px", display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
        <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.08em", color: C.faint2 }}>BUILT BY AHDI · ahdi@uaconsulting.co</div>
        <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.08em", color: C.faint2 }}>ALL DATA DERIVED FROM PUBLIC U.S. FEDERAL SOURCES · EPA · OSHA · USASPENDING</div>
      </div>
    </div>
  );
}

function badge(legalName: string, cust: boolean) {
  if (isGovernmentEndUser(legalName)) return { t: "GOVERNMENT END USER", c: C.muted2, b: C.chipBorder };
  return cust ? { t: "CUSTOMER", c: C.accent, b: C.accentDim } : { t: "PROSPECT", c: C.muted3, b: C.chipBorder };
}

function AccountsView({
  rollups,
  totalAccounts,
  isMobile,
  maxTcv,
  tierFilter,
  setTierFilter,
  onOpen,
}: {
  rollups: AccountRollup[];
  totalAccounts: number;
  isMobile: boolean;
  maxTcv: number;
  tierFilter: "whale" | "all";
  setTierFilter: (t: "whale" | "all") => void;
  onOpen: (id: string) => void;
}) {
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", padding: "14px 16px 10px", gap: 16, flexWrap: "wrap" }}>
        <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.14em", color: C.faint }}>PARENT ACCOUNTS · SORTED BY UNTOUCHED QUALIFIED TCV</div>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <div style={{ display: "flex", gap: 0 }}>
            {([["whale", "WHALE TIER ≥ $20M"], ["all", "ALL ACCOUNTS"]] as [("whale" | "all"), string][]).map(([val, label]) => {
              const on = tierFilter === val;
              return (
                <div
                  key={val}
                  onClick={() => setTierFilter(val)}
                  style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.1em", padding: "5px 9px", border: `1px solid ${on ? C.accentDim : C.chipBorder}`, color: on ? C.accent : C.muted3, cursor: "pointer", whiteSpace: "nowrap" }}
                >
                  {label}
                </div>
              );
            })}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.faint2 }}>{rollups.length} OF {totalAccounts} ACCOUNTS</div>
        </div>
      </div>

      {!isMobile ? (
        <div style={{ padding: "0 16px 28px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(180px,2fr) 1.1fr 92px 70px 70px 78px 210px", gap: 0, fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.1em", color: C.faint, borderBottom: `1px solid ${C.border}`, padding: "0 0 7px" }}>
            <div>ACCOUNT</div>
            <div>VERTICAL</div>
            <div>STATUS</div>
            <div style={{ textAlign: "right" }}>FAC</div>
            <div style={{ textAlign: "right" }}>QUAL</div>
            <div style={{ textAlign: "right" }}>UNTCHD</div>
            <div style={{ textAlign: "right" }}>UNTOUCHED QUALIFIED TCV</div>
          </div>
          {rollups.map((r) => {
            const bg = badge(r.account.legal_name, r.account.account_is_customer);
            const barPct = Math.round((r.untouchedTcv / (maxTcv || 1)) * 100);
            return (
              <div
                key={r.account.account_id}
                onClick={() => onOpen(r.account.account_id)}
                style={{ display: "grid", gridTemplateColumns: "minmax(180px,2fr) 1.1fr 92px 70px 70px 78px 210px", alignItems: "center", borderBottom: `1px solid ${C.rowBorder}`, padding: "11px 0", cursor: "pointer" }}
              >
                <div style={{ fontSize: 13.5, fontWeight: 500, color: C.text, paddingRight: 12, display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                  {r.account.website_url ? (
                    <a href={r.account.website_url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} style={{ color: C.text }}>
                      {resolveAccountDisplayName(r.account.legal_name, r.account.vertical_name)}
                    </a>
                  ) : (
                    resolveAccountDisplayName(r.account.legal_name, r.account.vertical_name)
                  )}
                  {r.account.linkedin_url && (
                    <a href={r.account.linkedin_url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} style={{ fontFamily: MONO, fontSize: 9, letterSpacing: "0.08em", color: C.faint, border: `1px solid ${C.chipBorder}`, padding: "1px 5px", whiteSpace: "nowrap" }}>
                      LINKEDIN
                    </a>
                  )}
                </div>
                <div style={{ fontSize: 12, color: C.muted3 }}>{r.account.vertical_name}</div>
                <div>
                  <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: "0.1em", padding: "3px 6px", border: `1px solid ${bg.b}`, color: bg.c }}>{bg.t}</span>
                </div>
                <div style={{ fontFamily: MONO, fontSize: 12, color: C.muted3, textAlign: "right" }}>
                  {r.account.total_facilities}
                  {r.account.apollo_employee_count != null && (
                    <div style={{ fontSize: 9, color: C.faint2, marginTop: 2 }}>{r.account.apollo_employee_count.toLocaleString()} EMP</div>
                  )}
                </div>
                <div style={{ fontFamily: MONO, fontSize: 12, color: C.muted2, textAlign: "right" }}>{r.qualifiedCount}</div>
                <div style={{ fontFamily: MONO, fontSize: 12, color: C.text, textAlign: "right" }}>{r.untouchedQualifiedCount}</div>
                <div style={{ textAlign: "right", paddingLeft: 16 }}>
                  <div style={{ fontFamily: MONO, fontSize: 15, color: C.accent }}>{money(r.untouchedTcv)}</div>
                  <div style={{ height: 3, background: "#1F262B", marginTop: 5 }}>
                    <div style={{ height: 3, background: C.accent, width: `${barPct}%` }} />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ padding: "0 12px 28px", display: "flex", flexDirection: "column", gap: 8 }}>
          {rollups.map((r) => {
            const bg = badge(r.account.legal_name, r.account.account_is_customer);
            return (
              <div key={r.account.account_id} onClick={() => onOpen(r.account.account_id)} style={{ background: C.panel, border: `1px solid ${C.border}`, padding: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                    <div style={{ fontSize: 15, fontWeight: 500, color: C.text }}>
                      {r.account.website_url ? (
                        <a href={r.account.website_url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} style={{ color: C.text }}>
                          {resolveAccountDisplayName(r.account.legal_name, r.account.vertical_name)}
                        </a>
                      ) : (
                        resolveAccountDisplayName(r.account.legal_name, r.account.vertical_name)
                      )}
                    </div>
                    {r.account.linkedin_url && (
                      <a href={r.account.linkedin_url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} style={{ fontFamily: MONO, fontSize: 9, letterSpacing: "0.08em", color: C.faint, border: `1px solid ${C.chipBorder}`, padding: "1px 5px", whiteSpace: "nowrap" }}>
                        LINKEDIN
                      </a>
                    )}
                  </div>
                  <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: "0.1em", padding: "3px 6px", border: `1px solid ${bg.b}`, color: bg.c, whiteSpace: "nowrap" }}>{bg.t}</span>
                </div>
                <div style={{ fontFamily: MONO, fontSize: 28, color: C.accent, marginTop: 10 }}>{money(r.untouchedTcv)}</div>
                <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.1em", color: C.faint, marginTop: 2 }}>UNTOUCHED QUALIFIED TCV</div>
                <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.muted3, marginTop: 10, borderTop: `1px solid ${C.border}`, paddingTop: 8 }}>
                  {r.untouchedQualifiedCount} untouched of {r.qualifiedCount} qualified · {r.account.total_facilities} plants
                  {r.account.apollo_employee_count != null && ` (${r.account.apollo_employee_count.toLocaleString()} Apollo employees)`}
                  {" · "}{r.account.vertical_name}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AccountDetailView({
  rollup,
  weights,
  statusFilter,
  setStatusFilter,
  threshold,
  onBack,
  onOpenFacility,
  onMap,
}: {
  rollup: AccountRollup;
  weights: Weights;
  statusFilter: "all" | "untouched" | "installed";
  setStatusFilter: (f: "all" | "untouched" | "installed") => void;
  threshold: number;
  onBack: () => void;
  onOpenFacility: (id: string) => void;
  onMap: () => void;
}) {
  const bg = badge(rollup.account.legal_name, rollup.account.account_is_customer);
  const tot = rollup.installedTcv + rollup.pipelineTcv + rollup.untouchedTcv || 1;
  const pInstalled = Math.round((rollup.installedTcv / tot) * 100);
  const pPipeline = Math.round((rollup.pipelineTcv / tot) * 100);
  const pUntouched = Math.round((rollup.untouchedTcv / tot) * 100);
  const installedCount = rollup.facilities.filter((f) => f.installed_status === "installed").length;

  const filtered = rollup.facilities
    .filter((f) => statusFilter === "all" || f.installed_status === statusFilter)
    .map((f) => ({ f, score: scoreFacility(f, weights) }))
    .sort((a, b) => b.score - a.score);

  return (
    <div style={{ padding: "14px 16px 28px" }}>
      <div onClick={onBack} style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.12em", color: C.faint, cursor: "pointer", marginBottom: 12 }}>
        ← ALL ACCOUNTS
      </div>
      <div style={{ background: C.panel, border: `1px solid ${C.border}`, padding: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <div style={{ fontSize: 22, fontWeight: 600 }}>{resolveAccountDisplayName(rollup.account.legal_name, rollup.account.vertical_name)}</div>
          <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: "0.1em", padding: "3px 6px", border: `1px solid ${bg.b}`, color: bg.c }}>{bg.t}</span>
          <span style={{ fontFamily: MONO, fontSize: 10, color: C.faint }}>{rollup.account.vertical_name}</span>
        </div>
        <div style={{ display: "flex", height: 26, marginTop: 18, background: C.bg }}>
          <div style={{ width: `${pInstalled}%`, background: C.installed }} />
          <div style={{ width: `${pPipeline}%`, background: C.pipeline }} />
          <div style={{ width: `${pUntouched}%`, background: C.accent }} />
        </div>
        <div style={{ display: "flex", gap: 28, marginTop: 10, flexWrap: "wrap" }}>
          <div style={{ whiteSpace: "nowrap" }}>
            <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.12em", color: C.faint }}>INSTALLED</div>
            <div style={{ fontFamily: MONO, fontSize: 15, color: C.muted3, marginTop: 3 }}>{money(rollup.installedTcv)}</div>
          </div>
          <div style={{ whiteSpace: "nowrap" }}>
            <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.12em", color: C.faint }}>IN PIPELINE</div>
            <div style={{ fontFamily: MONO, fontSize: 15, color: C.muted2, marginTop: 3 }}>{money(rollup.pipelineTcv)}</div>
          </div>
          <div style={{ whiteSpace: "nowrap" }}>
            <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.12em", color: C.accent }}>UNTOUCHED QUALIFIED</div>
            <div style={{ fontFamily: MONO, fontSize: 30, color: C.accent, marginTop: 1, lineHeight: 1.1 }}>{money(rollup.untouchedTcv)}</div>
          </div>
        </div>
        <div style={{ fontSize: 12.5, color: C.muted, marginTop: 12, maxWidth: 820 }}>
          {rollup.qualifiedCount} of {rollup.facilities.length} plants clear the signal threshold. Cells are installed in {installedCount}. {rollup.untouchedQualifiedCount} qualified plants have never been touched — that is {money(rollup.untouchedTcv)} inside
          {rollup.account.account_is_customer ? " a logo that already signs our paper." : " a logo that is not yet a customer."}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "18px 0 10px", flexWrap: "wrap" }}>
        <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.12em", color: C.faint, marginRight: 6 }}>FILTER</div>
        {([["ALL", "all"], ["UNTOUCHED ONLY", "untouched"], ["INSTALLED ONLY", "installed"]] as [string, typeof statusFilter][]).map(([label, val]) => {
          const on = statusFilter === val;
          return (
            <div key={val} onClick={() => setStatusFilter(val)} style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.1em", padding: "5px 9px", border: `1px solid ${on ? C.accentDim : C.chipBorder}`, color: on ? C.accent : C.muted3, cursor: "pointer", whiteSpace: "nowrap" }}>
              {label}
            </div>
          );
        })}
        <div onClick={onMap} style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.1em", padding: "5px 9px", border: `1px solid ${C.chipBorder}`, color: C.muted, cursor: "pointer", marginLeft: "auto", whiteSpace: "nowrap" }}>
          MAP VIEW →
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(200px,2.2fr) 1.1fr 110px 100px 60px 70px 130px", fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.1em", color: C.faint, borderBottom: `1px solid ${C.border}`, paddingBottom: 7 }}>
        <div>FACILITY</div>
        <div>CITY / STATE</div>
        <div style={{ textAlign: "right", paddingRight: 14 }}>SIGNAL</div>
        <div>STATUS</div>
        <div style={{ textAlign: "center" }}>QUAL</div>
        <div style={{ textAlign: "right" }}>CELLS</div>
        <div style={{ textAlign: "right" }}>FACILITY TCV</div>
      </div>
      {filtered.map(({ f, score }) => {
        const scoreColor = score >= 75 ? C.accent : score >= threshold ? C.muted2 : C.pipeline;
        const qualifies = f.qualified_for_tcv && score >= threshold;
        const statusColor = f.installed_status === "untouched" ? C.accent : f.installed_status === "in_pipeline" ? C.muted2 : C.pipeline;
        const tcvColor = qualifies && f.installed_status === "untouched" ? C.accent : C.muted3;
        return (
          <div key={f.facility_id} onClick={() => onOpenFacility(f.facility_id)} style={{ display: "grid", gridTemplateColumns: "minmax(200px,2.2fr) 1.1fr 110px 100px 60px 70px 130px", alignItems: "center", borderBottom: `1px solid ${C.rowBorder}`, padding: "10px 0", cursor: "pointer" }}>
            <div style={{ fontSize: 13, color: C.text, paddingRight: 12 }}>{resolveFacilityDisplayName(f.facility_name, f.epa_frs_name, rollup.account.legal_name, f.city)}</div>
            <div style={{ fontSize: 12, color: C.muted3 }}>{f.city}, {f.state}</div>
            <div style={{ textAlign: "right", paddingRight: 14 }}>
              <span style={{ fontFamily: MONO, fontSize: 13, color: scoreColor }}>{score}</span>
              <div style={{ height: 2, background: "#1F262B", marginTop: 4 }}>
                <div style={{ height: 2, background: scoreColor, width: `${Math.max(0, Math.min(100, score))}%` }} />
              </div>
            </div>
            <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.08em", color: statusColor }}>{f.installed_status.toUpperCase()}</div>
            <div style={{ fontFamily: MONO, fontSize: 11, color: qualifies ? C.muted2 : C.faint2, textAlign: "center" }}>{qualifies ? "YES" : "—"}</div>
            <div style={{ fontFamily: MONO, fontSize: 12, color: C.muted2, textAlign: "right" }}>{f.est_cells_capacity}</div>
            <div style={{ fontFamily: MONO, fontSize: 13, color: tcvColor, textAlign: "right" }}>
              {money(f.est_facility_tcv)}
              {f.tcv_is_derived && <span title="Derived estimate, not a real OSHA employee count" style={{ marginLeft: 4, color: C.accent }}>*</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FacilityDetailView({
  facility,
  account,
  weights,
  threshold,
  onBack,
}: {
  facility: Facility;
  account: Account;
  weights: Weights;
  threshold: number;
  onBack: () => void;
}) {
  const score = scoreFacility(facility, weights);
  const qualifies = facility.qualified_for_tcv && score >= threshold;
  const np = nSignalsPresent(facility);
  const bg = badge(account.legal_name, account.account_is_customer);
  const tcvColor = qualifies && facility.installed_status === "untouched" ? C.accent : C.muted2;
  const statusColor = facility.installed_status === "untouched" ? C.accent : C.muted;

  const signalKeys: SignalKey[] = ["voc", "trend", "dart", "dod", "size"];

  return (
    <div style={{ padding: "14px 16px 28px" }}>
      <div onClick={onBack} style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.12em", color: C.faint, cursor: "pointer", marginBottom: 12 }}>
        ← {resolveAccountDisplayName(account.legal_name, account.vertical_name).toUpperCase()} FACILITIES
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1.35fr 1fr", gap: 14 }}>
        <div style={{ background: C.panel, border: `1px solid ${C.border}`, padding: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
            <div>
              <div style={{ fontSize: 20, fontWeight: 600 }}>{resolveFacilityDisplayName(facility.facility_name, facility.epa_frs_name, account.legal_name, facility.city)}</div>
              <div style={{ fontFamily: MONO, fontSize: 11, color: C.faint, marginTop: 4 }}>
                {resolveAccountDisplayName(account.legal_name, account.vertical_name)} · {facility.city}, {facility.state} · {facility.facility_vertical_name}
              </div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.12em", color: C.faint }}>FACILITY TCV</div>
              <div style={{ fontFamily: MONO, fontSize: 30, color: tcvColor, lineHeight: 1.15 }}>{money(facility.est_facility_tcv)}</div>
              <div style={{ fontFamily: MONO, fontSize: 10, color: C.faint }}>
                {facility.est_cells_capacity} CELLS EST. CAPACITY{facility.tcv_is_derived ? " · DERIVED ESTIMATE" : ""}
              </div>
            </div>
          </div>

          <div style={{ display: "flex", gap: 10, marginTop: 14, flexWrap: "wrap" }}>
            <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.08em", border: `1px solid ${C.chipBorder}`, padding: "5px 8px", color: np === 5 ? C.muted : C.accent }}>{np} OF 5 SIGNALS PRESENT</div>
            <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.08em", border: `1px solid ${C.chipBorder}`, padding: "5px 8px", color: C.muted }}>COMPOSITE SIGNAL {score}</div>
            <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.08em", border: `1px solid ${C.chipBorder}`, padding: "5px 8px", color: statusColor }}>{facility.installed_status.toUpperCase()}</div>
            {facility.tcv_is_derived && (
              <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.08em", border: `1px solid ${C.accentDim}`, padding: "5px 8px", color: C.accent }}>TCV DERIVED ({facility.tcv_basis.replace(/_/g, " ").toUpperCase()})</div>
            )}
          </div>

          <div style={{ marginTop: 22, display: "flex", flexDirection: "column", gap: 16 }}>
            {signalKeys.map((k) => {
              const sig = facility.signals[k];
              const barColor = sig.present ? (sig.score >= 70 ? C.accent : "#4E5C65") : C.border;
              return (
                <div key={k}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
                    <div style={{ fontSize: 13, color: sig.present ? C.text2 : C.faint2 }}>{SIGNAL_LABELS[k]}</div>
                    <div style={{ fontFamily: MONO, fontSize: 13, color: sig.present ? (sig.score >= 70 ? C.accent : C.muted2) : C.faint2 }}>{sig.present ? sig.score : "NO DATA"}</div>
                  </div>
                  <div style={{ height: 8, background: C.bg, marginTop: 6, border: `1px solid #1F262B` }}>
                    <div style={{ height: 6, background: barColor, width: `${sig.present ? sig.score : 0}%` }} />
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginTop: 5, flexWrap: "wrap" }}>
                    <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted2 }}>{sig.present ? sig.raw : "Signal absent from source records — excluded from the composite"}</div>
                    <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.08em", color: C.faint2 }}>{sig.source}</div>
                  </div>
                  {sig.note && (
                    <div style={{ marginTop: 6, fontFamily: MONO, fontSize: 10, letterSpacing: "0.06em", color: C.bg, background: C.accent, display: "inline-block", padding: "3px 7px" }}>{sig.note}</div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ background: C.panel, border: `1px solid ${C.border}`, padding: 16 }}>
            <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.14em", color: C.accent }}>WHY NOW</div>
            <div style={{ fontSize: 14, lineHeight: 1.6, color: C.text2, marginTop: 10 }}>{facility.why_now}</div>
          </div>
          <div style={{ background: C.panel, border: `1px solid ${C.border}`, padding: 16 }}>
            <div style={{ fontFamily: MONO, fontSize: 9.5, letterSpacing: "0.14em", color: C.faint }}>ATTRIBUTION &amp; CONFIDENCE</div>
            <div style={{ fontSize: 12.5, lineHeight: 1.55, color: C.muted, marginTop: 10 }}>
              {facility.match_reason}
              {np < 5 ? ` ${5 - np} of 5 signals are missing from the source records; the composite is computed only over the signals present, so it is less defensible than a 5-of-5 score of the same value.` : " All five signals are present."}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.faint2, marginTop: 12, borderTop: `1px solid ${C.border}`, paddingTop: 10 }}>
              INTERNAL {facility.facility_id} &nbsp;·&nbsp; SOURCES {facility.member_source_ids.join(", ")} &nbsp;·&nbsp; COORD {facility.suspect_coordinates ? "FLAGGED SUSPECT" : "VERIFIED"}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function MapView({
  facilities,
  accounts,
  title,
  zoom,
  setZoom,
  onOpenFacility,
}: {
  facilities: Facility[];
  accounts: Account[];
  title: string;
  zoom: number;
  setZoom: (n: number) => void;
  onOpenFacility: (id: string) => void;
}) {
  const accountNames: Record<string, string> = {};
  accounts.forEach((a) => { accountNames[a.account_id] = a.legal_name; });
  const visible = facilities.filter((f) => !f.suspect_coordinates && f.latitude != null && f.longitude != null);
  const excluded = facilities.length - visible.length;
  const proj = (lat: number, lng: number) => ({
    x: Math.max(1, Math.min(99, ((lng + 126) / 60) * 100)),
    y: Math.max(1, Math.min(99, ((50.5 - lat) / 26) * 100)),
  });

  type Dot = { x: number; y: number; size: number; fill: string; isCluster: boolean; count: string | number; title: string; onClick: () => void };
  let dots: Dot[] = [];

  if (zoom < 2) {
    const byState: Record<string, { n: number; tcv: number; lat: number; lng: number; unt: number }> = {};
    visible.forEach((f) => {
      const k = f.state ?? "??";
      if (!byState[k]) byState[k] = { n: 0, tcv: 0, lat: 0, lng: 0, unt: 0 };
      const b = byState[k];
      b.n++;
      b.tcv += f.est_facility_tcv;
      b.lat += f.latitude!;
      b.lng += f.longitude!;
      if (f.installed_status === "untouched") b.unt++;
    });
    dots = Object.entries(byState).map(([k, b]) => {
      const p = proj(b.lat / b.n, b.lng / b.n);
      const size = Math.round(16 + Math.sqrt(b.tcv / 1e6) * 2.4);
      return { x: p.x, y: p.y, size, fill: b.unt / b.n > 0.5 ? C.accent : C.pipeline, isCluster: true, count: b.n, title: `${k} · ${b.n} plants · ${money(b.tcv)}`, onClick: () => setZoom(2) };
    });
  } else {
    dots = visible.map((f) => {
      const p = proj(f.latitude!, f.longitude!);
      const size = Math.round(((5 + Math.sqrt(f.est_facility_tcv / 1e6) * 2.6) / zoom) * 1.4);
      const fill = f.installed_status === "untouched" ? C.accent : f.installed_status === "in_pipeline" ? C.pipeline : C.installed;
      const displayName = resolveFacilityDisplayName(f.facility_name, f.epa_frs_name, accountNames[f.account_id] ?? "Unknown account", f.city);
      return { x: p.x, y: p.y, size, fill, isCluster: false, count: "", title: `${displayName} · ${money(f.est_facility_tcv)} · ${f.installed_status.toUpperCase()}`, onClick: () => onOpenFacility(f.facility_id) };
    });
  }

  return (
    <div style={{ padding: "14px 16px 28px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 10 }}>
        <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.14em", color: C.faint }}>
          {title}
          {zoom < 2 ? " · CLUSTERED BY STATE" : " · INDIVIDUAL PLANTS"}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.faint2 }}>ZOOM {zoom}</div>
          <div onClick={() => setZoom(Math.max(1, zoom - 1))} style={{ fontFamily: MONO, fontSize: 12, border: `1px solid ${C.chipBorder}`, padding: "2px 11px", cursor: "pointer", color: C.muted }}>−</div>
          <div onClick={() => setZoom(Math.min(3, zoom + 1))} style={{ fontFamily: MONO, fontSize: 12, border: `1px solid ${C.chipBorder}`, padding: "2px 11px", cursor: "pointer", color: C.muted }}>+</div>
        </div>
      </div>
      <div style={{ position: "relative", background: C.panel2, border: `1px solid ${C.border}`, height: "62vh", minHeight: 420, overflow: "hidden" }}>
        <div style={{ position: "absolute", inset: 0, backgroundImage: `linear-gradient(#1B2126 1px,transparent 1px),linear-gradient(90deg,#1B2126 1px,transparent 1px)`, backgroundSize: "56px 56px", opacity: 0.6 }} />
        <div style={{ position: "absolute", inset: 0, transform: `scale(${zoom < 2 ? 1 : zoom === 2 ? 1 : 1.7})`, transformOrigin: "50% 50%" }}>
          {dots.map((d, i) => (
            <div key={i} onClick={d.onClick} title={d.title} style={{ position: "absolute", left: `${d.x}%`, top: `${d.y}%`, width: d.size, height: d.size, marginLeft: -d.size / 2, marginTop: -d.size / 2, background: d.fill, border: `1px solid ${C.bg}`, borderRadius: "50%", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center" }}>
              {d.isCluster && <span style={{ fontFamily: MONO, fontSize: 9, color: C.bg, fontWeight: 600 }}>{d.count}</span>}
            </div>
          ))}
        </div>
        <div style={{ position: "absolute", left: 14, bottom: 14, background: C.bg, border: `1px solid ${C.border}`, padding: "10px 12px" }}>
          <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: "0.12em", color: C.faint }}>LEGEND</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7 }}><span style={{ width: 9, height: 9, background: C.accent, borderRadius: "50%", display: "block" }} /><span style={{ fontFamily: MONO, fontSize: 10, color: C.muted2 }}>UNTOUCHED</span></div>
            {/* PIPELINE/INSTALLED: no source populates either flag today (see the
                PROVENANCE note above the fold) -- greyed out with an explicit
                reason rather than shown as if real data feeds them. */}
            <div style={{ display: "flex", alignItems: "center", gap: 7, opacity: 0.4 }}><span style={{ width: 9, height: 9, background: C.pipeline, borderRadius: "50%", display: "block" }} /><span style={{ fontFamily: MONO, fontSize: 10, color: C.muted2 }}>PIPELINE (REQUIRES CRM)</span></div>
            <div style={{ display: "flex", alignItems: "center", gap: 7, opacity: 0.4 }}><span style={{ width: 9, height: 9, background: C.installed, borderRadius: "50%", display: "block" }} /><span style={{ fontFamily: MONO, fontSize: 10, color: C.muted2 }}>INSTALLED (REQUIRES CRM)</span></div>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.faint2, marginTop: 4 }}>DOT AREA ∝ FACILITY TCV</div>
          </div>
        </div>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.faint2, marginTop: 8 }}>
        {excluded} facilities excluded from this map — suspect coordinates in source records. They stay in the ranking and out of the map.
      </div>
    </div>
  );
}

function ReviewQueueView({ reviews }: { reviews: PendingReview[] | null }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  if (!reviews) {
    return <div style={{ padding: 16, fontFamily: MONO, fontSize: 11, color: C.faint }}>LOADING REVIEW QUEUE…</div>;
  }

  // Grouped by parent account -- a flat stack of 587 items is unworkable
  // when one account (Thor Motor Coach) can carry ~20 of them on its own.
  // Collapsed by default; sorted by group size descending so the accounts
  // actually worth a bulk decision surface first, not buried alphabetically.
  const byAccount = new Map<string, PendingReview[]>();
  reviews.forEach((r) => {
    if (!byAccount.has(r.account_id)) byAccount.set(r.account_id, []);
    byAccount.get(r.account_id)!.push(r);
  });
  const groups = [...byAccount.entries()]
    .map(([accountId, items]) => ({
      accountId,
      legalName: items[0].legal_name,
      items: [...items].sort((a, b) => (a.name_similarity ?? 1) - (b.name_similarity ?? 1)),
    }))
    .sort((a, b) => b.items.length - a.items.length);

  function toggle(accountId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(accountId)) next.delete(accountId);
      else next.add(accountId);
      return next;
    });
  }

  return (
    <div style={{ padding: "14px 16px 28px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 16, flexWrap: "wrap", marginBottom: 12 }}>
        <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.14em", color: C.faint }}>ENTITY RESOLUTION · PENDING HUMAN CONFIRMATION</div>
        <div style={{ fontSize: 12, color: C.faint, maxWidth: 620 }}>{reviews.length} candidate merges across {groups.length} accounts the resolver would not auto-confirm — Tier 3 (proximity match, name similarity below gate) or cluster-diameter overflow.</div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {groups.map((g) => {
          const isOpen = expanded.has(g.accountId);
          return (
            <div key={g.accountId} style={{ background: C.panel, border: `1px solid ${C.border}` }}>
              <div
                onClick={() => toggle(g.accountId)}
                style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, padding: "12px 16px", cursor: "pointer", flexWrap: "wrap" }}
              >
                <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                  <div style={{ fontSize: 14, fontWeight: 500 }}>{resolveAccountDisplayName(g.legalName, null)}</div>
                  <div style={{ fontFamily: MONO, fontSize: 11, color: C.faint }}>{g.accountId}</div>
                  <span style={{ fontFamily: MONO, fontSize: 9, letterSpacing: "0.1em", padding: "2px 6px", border: `1px solid ${C.chipBorder}`, color: C.accent }}>{g.items.length} PENDING</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div
                    onClick={(e) => { e.stopPropagation(); }}
                    style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.1em", border: `1px solid ${C.chipBorder}`, padding: "5px 10px", cursor: "pointer", color: C.muted2, whiteSpace: "nowrap" }}
                  >
                    CONFIRM ALL FOR THIS ACCOUNT
                  </div>
                  <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.12em", color: C.muted, whiteSpace: "nowrap" }}>{isOpen ? "COLLAPSE −" : "EXPAND +"}</div>
                </div>
              </div>
              {isOpen && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "0 16px 14px" }}>
                  {g.items.map((r, i) => {
                    const conf = r.name_similarity;
                    const confColor = conf != null && conf > 0.8 ? C.muted : C.accent;
                    return (
                      <div key={i} style={{ background: C.panel2, border: `1px solid ${C.border}`, borderLeft: `2px solid ${C.chipBorder}`, padding: "12px 14px" }}>
                        <div style={{ display: "flex", justifyContent: "flex-end", gap: 26, flexWrap: "wrap" }}>
                          <div>
                            <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: "0.12em", color: C.faint }}>DISTANCE</div>
                            <div style={{ fontFamily: MONO, fontSize: 16, color: C.accent, marginTop: 2 }}>{r.distance_m != null ? `${r.distance_m.toFixed(0)}m` : "—"}</div>
                          </div>
                          <div>
                            <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: "0.12em", color: C.faint }}>NAME SIMILARITY</div>
                            <div style={{ fontFamily: MONO, fontSize: 16, color: confColor, marginTop: 2 }}>{conf != null ? conf.toFixed(2) : "—"}</div>
                          </div>
                        </div>
                        <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted2, marginTop: 8, lineHeight: 1.7 }}>
                          A&nbsp;&nbsp;{r.record_a}<br />
                          B&nbsp;&nbsp;{r.record_b}
                        </div>
                        <div style={{ fontSize: 12.5, color: C.muted, marginTop: 10 }}>{r.reason}</div>
                        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                          <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.1em", border: `1px solid ${C.chipBorder}`, padding: "5px 10px", cursor: "pointer", color: C.muted2, whiteSpace: "nowrap" }}>CONFIRM MERGE</div>
                          <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.1em", border: `1px solid ${C.chipBorder}`, padding: "5px 10px", cursor: "pointer", color: C.faint, whiteSpace: "nowrap" }}>KEEP SEPARATE</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function BriefView() {
  return (
    <div style={{ padding: "14px 16px 28px", maxWidth: 780 }}>
      <div style={{ fontFamily: MONO, fontSize: 10, letterSpacing: "0.14em", color: C.faint, marginBottom: 12 }}>BRIEF</div>
      <div style={{ fontSize: 14, lineHeight: 1.6, color: C.text }}>
        Whale Engine ranks individual manufacturing plants by how much robotic surface finishing work is likely sitting inside them, then rolls those plants up to the parent company that owns them.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 20 }}>
        <div style={{ fontSize: 13, lineHeight: 1.5, color: C.muted }}>
          <span style={{ color: C.faint, fontFamily: MONO, fontSize: 11, letterSpacing: "0.08em" }}>SOURCES&nbsp;&nbsp;</span>
          EPA Facility Registry Service and National Emissions Inventory · OSHA Injury Tracking Application · USASpending federal contract awards.
        </div>
        <div style={{ fontSize: 13, lineHeight: 1.5, color: C.muted }}>
          <span style={{ color: C.faint, fontFamily: MONO, fontSize: 11, letterSpacing: "0.08em" }}>UNTOUCHED QUALIFIED TCV&nbsp;&nbsp;</span>
          Contract value in plants that clear the signal threshold and have no cell installed and no open pipeline — most of it inside logos we have already landed.
        </div>
        <div style={{ fontSize: 13, lineHeight: 1.5, color: C.muted }}>
          <span style={{ color: C.faint, fontFamily: MONO, fontSize: 11, letterSpacing: "0.08em" }}>STATUS&nbsp;&nbsp;</span>
          The Facility Signal Engine (Agent 1, which resolves and ranks plants — this app) is live on real data. Four additional agents in the planned system — Part Fit Qualifier, Multithread Map, Capital Case Builder, Expansion Engine — are architecture, not built.
        </div>
        <div style={{ fontSize: 13, lineHeight: 1.5, color: C.muted }}>
          <span style={{ color: C.faint, fontFamily: MONO, fontSize: 11, letterSpacing: "0.08em" }}>PROVENANCE&nbsp;&nbsp;</span>
          Customer status is inferred from the public logo wall, not a CRM. Installed and pipeline flags require a CRM join and are currently unpopulated, so every qualified plant defaults to untouched.
        </div>
      </div>
    </div>
  );
}
