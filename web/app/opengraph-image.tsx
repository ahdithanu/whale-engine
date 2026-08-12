import { ImageResponse } from "next/og";
import { loadBootstrapData, computeHeadline } from "@/lib/serverData";
import { money } from "@/lib/scoring";

export const alt = "Whale Engine — Facility Signal Engine";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// Same dark/amber palette as web/app/page.tsx's C object, kept in sync by
// hand since this route can't import client-component-scoped constants.
const BG = "#0E1113";
const BORDER = "#232A2F";
const TEXT = "#E6EAEC";
const MUTED = "#9DAAB2";
const ACCENT = "#F5A623";

export default async function Image() {
  const { accounts, facilities } = await loadBootstrapData();
  const h = computeHeadline(accounts, facilities);

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: BG,
          padding: 64,
          fontFamily: "monospace",
        }}
      >
        <div style={{ display: "flex", alignItems: "baseline", gap: 16 }}>
          <div style={{ display: "flex", fontSize: 28, fontWeight: 600, color: TEXT, letterSpacing: 2 }}>WHALE ENGINE</div>
          <div style={{ display: "flex", fontSize: 18, color: MUTED, letterSpacing: 1 }}>FACILITY SIGNAL ENGINE</div>
        </div>

        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", fontSize: 22, color: ACCENT, letterSpacing: 2, marginBottom: 12 }}>
            UNTOUCHED TCV INSIDE EXISTING CUSTOMERS
          </div>
          <div style={{ display: "flex", fontSize: 128, fontWeight: 700, color: ACCENT, lineHeight: 1, letterSpacing: -2 }}>
            {money(h.customerUntouchedTcv)}
          </div>
          <div style={{ display: "flex", gap: 40, marginTop: 36 }}>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <div style={{ display: "flex", fontSize: 40, color: TEXT, fontWeight: 600 }}>{h.whaleCount}</div>
              <div style={{ display: "flex", fontSize: 18, color: MUTED, letterSpacing: 1 }}>WHALE ACCOUNTS AT $20M+</div>
            </div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              <div style={{ display: "flex", fontSize: 40, color: TEXT, fontWeight: 600 }}>{h.qualifiedFacilityCount}</div>
              <div style={{ display: "flex", fontSize: 18, color: MUTED, letterSpacing: 1 }}>QUALIFIED PLANTS</div>
            </div>
          </div>
        </div>

        <div
          style={{
            display: "flex",
            borderTop: `1px solid ${BORDER}`,
            paddingTop: 24,
            fontSize: 16,
            color: MUTED,
            letterSpacing: 1,
          }}
        >
          ALL DATA DERIVED FROM PUBLIC U.S. FEDERAL SOURCES - EPA - OSHA - USASPENDING
        </div>
      </div>
    ),
    { ...size }
  );
}
