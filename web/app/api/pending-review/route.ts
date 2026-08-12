import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import path from "path";
import { PendingReview } from "@/lib/types";

const DATA_DIR = path.join(process.cwd(), "public", "data");

// Fetched lazily, only when the Review Queue view opens -- not part of the
// main bootstrap payload, per "hit API routes only for detail views".
export async function GET() {
  const raw = await readFile(path.join(DATA_DIR, "pending_review.json"), "utf-8");
  const reviews: PendingReview[] = JSON.parse(raw);
  return NextResponse.json({ reviews });
}
