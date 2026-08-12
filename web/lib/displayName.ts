// OSHA establishment names are free text typed by whoever filed the injury
// report -- bare addresses ("Northland Dr."), bare cities ("Jamestown"),
// generic labels ("Plant 1", "Machine Shop"), numeric site codes
// ("8403_19144"), and metadata strings all show up as facility_name because
// there's no cleaner field to fall back on at ingestion time. This resolver
// rejects those patterns and falls back to a more reliable name instead of
// rendering the junk directly.

const GENERIC_LABELS = [
  "plant",
  "building",
  "warehouse",
  "service",
  "parts",
  "admin",
  "administration",
  "headquarters",
  "main facility",
  "corporate",
  "audit",
  "machine shop",
  "facility",
  "site",
  "location",
  "office",
  "hq",
];

const US_STATE_CODES = new Set([
  "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
  "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
  "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
  "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
]);

// Trailing legal-entity tokens that can be mistaken for a two-letter state
// code by a naive regex (e.g. "Lyndon Steel Company, LP") -- checked before
// treating a candidate as a bare "City, ST" string.
const LEGAL_SUFFIX = /,\s*(inc|llc|lp|co|corp|ltd|plc)\.?$/i;

function isPurelyNumericOrCode(s: string): boolean {
  return /^[\d\s_.\-]+$/.test(s);
}

function isBareCityState(s: string): boolean {
  if (LEGAL_SUFFIX.test(s)) return false;
  const m = s.match(/^([A-Za-z .'-]+),\s*([A-Za-z]{2})$/);
  if (!m) return false;
  return US_STATE_CODES.has(m[2].toUpperCase());
}

function isBareStreetAddress(s: string): boolean {
  // e.g. "124 Gwyn Dr", "33964 N Main St", "Kelley St" -- an optional
  // leading house number plus a short street-suffix-terminated string, not
  // a company name. Capped at 4 words so a real name that happens to end in
  // a word like "Way" or "Court" isn't caught (few company names are this
  // short AND end directly in a bare suffix token).
  return /^(\d+\s+)?[\w.]+(\s+[\w.]+){0,2}\s+(st|rd|dr|ave|ln|blvd|hwy|ct|pl|way)\.?$/i.test(s);
}

function isGenericLabel(s: string): boolean {
  const norm = s.toLowerCase().replace(/\s+/g, " ").trim();
  return GENERIC_LABELS.some((label) => norm === label || new RegExp(`^${label}\\s*#?\\d+$`).test(norm));
}

/** True if `s` looks like a real, renderable facility/account name -- not a
 * bare code, address, city/state pair, or generic label. */
export function isValidDisplayName(s: string | null | undefined): boolean {
  if (!s) return false;
  const trimmed = s.trim();
  if (trimmed.length < 3) return false;
  if (isPurelyNumericOrCode(trimmed)) return false;
  if (isBareCityState(trimmed)) return false;
  if (isBareStreetAddress(trimmed)) return false;
  if (isGenericLabel(trimmed)) return false;
  return true;
}

/** Resolves the name to show for a facility: its own name if valid, else the
 * EPA FRS registration name (filed by the facility itself, not typed
 * freehand into an OSHA form), else the parent account name plus city. */
export function resolveFacilityDisplayName(
  facilityName: string | null | undefined,
  epaFrsName: string | null | undefined,
  accountName: string,
  city: string | null | undefined
): string {
  if (isValidDisplayName(facilityName)) return facilityName!.trim();
  if (isValidDisplayName(epaFrsName)) return epaFrsName!.trim();
  return city ? `${accountName} — ${city}` : accountName;
}

/** Resolves the name to show for an account. Accounts have no EPA-FRS-style
 * fallback of their own (they're already the parent), so an invalid
 * legal_name falls back to a plainly-labeled placeholder rather than
 * inventing a company name. Rare in practice -- almost every account name in
 * this dataset is already a real company name. */
export function resolveAccountDisplayName(legalName: string, verticalName: string | null | undefined): string {
  if (isValidDisplayName(legalName)) return legalName.trim();
  return verticalName ? `Unresolved account — ${verticalName}` : "Unresolved account";
}
