import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import path from "path";
import { Account, Facility, Meta } from "@/lib/types";

const DATA_DIR = path.join(process.cwd(), "public", "data");

export async function GET() {
  const [accountsRaw, facilitiesRaw, metaRaw] = await Promise.all([
    readFile(path.join(DATA_DIR, "accounts.json"), "utf-8"),
    readFile(path.join(DATA_DIR, "facilities.json"), "utf-8"),
    readFile(path.join(DATA_DIR, "meta.json"), "utf-8"),
  ]);
  const accounts: Account[] = JSON.parse(accountsRaw);
  const facilities: Facility[] = JSON.parse(facilitiesRaw);
  const meta: Meta = JSON.parse(metaRaw);
  return NextResponse.json({ accounts, facilities, meta });
}
