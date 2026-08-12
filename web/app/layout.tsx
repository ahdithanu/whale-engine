import type { Metadata } from "next";
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import "./globals.css";
import { loadBootstrapData, computeHeadline } from "@/lib/serverData";
import { money } from "@/lib/scoring";

const plexSans = IBM_Plex_Sans({
  variable: "--font-plex-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

// Dynamic, not static: a hardcoded title/description would go stale the
// moment the underlying data changes (exactly what left this link dead on
// arrival when shared -- no OG tags at all, and any static claim baked in
// here would eventually be a lie). Computed the same way the page itself
// computes its headline numbers (see lib/serverData.ts), so "17 whale
// accounts" in a Slack preview always matches what the link actually shows.
// Vercel sets these automatically per-deployment (production vs. preview) --
// using them instead of a hardcoded domain means og:image/twitter:image
// resolve correctly wherever this actually lands, not a guessed URL.
const VERCEL_HOST = process.env.VERCEL_PROJECT_PRODUCTION_URL ?? process.env.VERCEL_URL;
const SITE_URL = VERCEL_HOST ? `https://${VERCEL_HOST}` : "http://localhost:3000";

export async function generateMetadata(): Promise<Metadata> {
  const { accounts, facilities } = await loadBootstrapData();
  const h = computeHeadline(accounts, facilities);
  const title = `${h.whaleCount} whale accounts. ${money(h.whaleTcv)} untouched inside the existing logo wall.`;
  const description =
    `Facility-level account intelligence from EPA (Facility Registry Service + National Emissions Inventory), ` +
    `OSHA (Injury Tracking Application), and USASpending federal contract awards. ` +
    `${h.qualifiedFacilityCount} qualified plants across ${h.totalFacilities.toLocaleString()} resolved facilities.`;
  return {
    metadataBase: new URL(SITE_URL),
    title,
    description,
    openGraph: {
      title,
      description,
      type: "website",
      images: ["/opengraph-image"],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: ["/opengraph-image"],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${plexSans.variable} ${plexMono.variable}`}>{children}</body>
    </html>
  );
}
