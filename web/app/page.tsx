import AppClient from "./AppClient";
import { loadBootstrapData } from "@/lib/serverData";

// Server Component: fetches the same static JSON the old client-only
// useEffect fetched, but during the server render, not after hydration.
// That's the actual fix for "curl only returns LOADING WHALE ENGINE" -- the
// data (and therefore the real headline numbers) is now part of the first
// HTML the server sends, not something that only shows up once JS runs in
// a browser. AppClient stays a client component for the interactive parts
// (weight sliders, view routing, map zoom); it just receives real data as
// props instead of fetching it itself.
export default async function Page() {
  const { accounts, facilities } = await loadBootstrapData();
  return <AppClient initialAccounts={accounts} initialFacilities={facilities} />;
}
