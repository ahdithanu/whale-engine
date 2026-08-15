export type Signal = {
  score: number;
  present: boolean;
  raw: string;
  source: string;
  note?: string | null;
};

export type FacilitySignals = {
  voc: Signal;
  trend: Signal; // VOC trend — real weighted signal, not DART trend
  dart: Signal; // DART rate + trend, blended server-side (0.7 rate / 0.3 trend)
  dod: Signal;
  size: Signal; // employee count band + air permit class, blended 60/40 (no real sqft source exists)
};

export type Facility = {
  facility_id: string;
  account_id: string;
  facility_name: string;
  // First fallback for the display-name resolver (lib/displayName.ts) when
  // facility_name is OSHA free-text junk. Null if this facility has no EPA
  // member at all.
  epa_frs_name: string | null;
  city: string | null;
  state: string | null;
  latitude: number | null;
  longitude: number | null;
  suspect_coordinates: boolean;
  facility_vertical_key: string | null;
  facility_vertical_name: string;
  match_tier: string;
  match_confidence: number;
  match_reason: string;
  qualified_for_tcv: boolean;
  qualification_reason: string;
  installed_status: "installed" | "in_pipeline" | "untouched";
  // Real CRM join point, not real CRM data yet -- null until a CRM export
  // exists (see pipeline/export.py CRM_STATUS_OVERRIDE_PATH). Not read
  // anywhere in the UI yet; installed_status remains the source of truth
  // until this is wired up.
  crm_status: string | null;
  est_cells_capacity: number;
  est_facility_tcv: number;
  est_finishing_headcount: number | null;
  tcv_basis: "actual" | "voc_estimated" | "pm_estimated" | "vertical_median_estimated" | "no_estimate" | "not_qualified";
  tcv_is_derived: boolean;
  facility_score: number;
  why_now: string;
  member_source_ids: string[];
  sources: string[];
  signals: FacilitySignals;
};

export type Account = {
  account_id: string;
  legal_name: string;
  vertical_key: string | null;
  vertical_name: string;
  account_is_customer: boolean;
  total_facilities: number;
  qualified_facilities: number;
  untouched_qualified_facilities: number;
  pending_review_count: number;
  installed_tcv: number;
  pipeline_tcv: number;
  untouched_tcv: number;
  // Apollo company enrichment (top 50 accounts by untouched_tcv only) --
  // null for every other account, and null for top-50 accounts Apollo
  // couldn't confidently match to a real company domain.
  website_url: string | null;
  linkedin_url: string | null;
  hq_city: string | null;
  hq_state: string | null;
  apollo_employee_count: number | null;
  // Hand-curated "why call them this week" note (pipeline/overrides/news_notes.yaml)
  // -- not a live news feed. Null for every account until a human adds one.
  news_note: { text: string; url: string; date: string } | null;
};

// Government end users get a distinct badge and are excluded from the
// whale-tier headline count/TCV -- a Naval Shipyard and Boeing are not the
// same sales motion (federal procurement vs. commercial), and folding a
// branch of the armed forces into "whale accounts alongside Lockheed and
// Boeing" reads as a data error to an analytical reader even when the
// underlying facility signal is real. Deliberately narrow: only the literal
// service-branch accounts (the four that actually appear in the resolved
// universe today), not every GOCO site whose name happens to contain "Air
// Force" -- e.g. an Air Force Plant number is typically a government-owned,
// contractor-operated site run by a real manufacturer, a different case
// this list is not trying to catch without more research.
const GOVERNMENT_END_USER_NAMES = new Set([
  "u.s. navy",
  "u.s. air force",
  "u.s. army",
  "u.s. marine corps",
  "u.s. coast guard",
  "u.s. space force",
]);

export function isGovernmentEndUser(legalName: string): boolean {
  return GOVERNMENT_END_USER_NAMES.has(legalName.trim().toLowerCase());
}

export type PendingReview = {
  account_id: string;
  legal_name: string;
  record_a: string;
  record_b: string;
  distance_m: number | null;
  name_similarity: number | null;
  reason: string;
};

export type Meta = {
  generated_at: string;
  total_accounts_exported: number;
  total_facilities_exported: number;
  customer_accounts: number;
  total_untouched_tcv: number;
  customer_untouched_tcv: number;
  total_qualified_facilities: number;
  pending_review_count: number;
};

export type Bootstrap = {
  accounts: Account[];
  facilities: Facility[];
  meta: Meta;
};

export type SignalKey = keyof FacilitySignals;

export type Weights = Record<SignalKey, number>;

export const DEFAULT_WEIGHTS: Weights = {
  voc: 22,
  trend: 8,
  dart: 25,
  dod: 20,
  size: 25,
};

export const SIGNAL_LABELS: Record<SignalKey, string> = {
  voc: "VOC tonnage",
  trend: "VOC trend",
  dart: "DART rate + trend",
  dod: "DoD awards",
  size: "Employee count + air permit",
};

export const SIGNAL_SOURCES: Record<SignalKey, string> = {
  voc: "EPA NEI",
  trend: "EPA NEI DELTA",
  dart: "OSHA ITA",
  dod: "USASPENDING",
  size: "OSHA ITA / EPA FRS",
};
