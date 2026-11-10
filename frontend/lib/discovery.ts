import type { TravelerProfile } from './traveler-profile.ts';
export type Destination = { geoname_id: number; city: string; country: string; country_code: string; continent: string };
export type Evidence = { chunk_id: number; section: string; snippet: string; source: string; title: string; revision_id: number; url: string; license: string; ingested_at: string; content_hash: string };
export type DiscoveryResult = Destination & { rank: number; reasons: string[]; evidence: Evidence[]; dense_rank: number | null; lexical_rank: number | null; retrieval_score: number };
export type DiscoveryResponse = { query: string; duration_days: number; limitations: string[]; method: string; preference_handling: string; results?: DiscoveryResult[]; destination?: Destination; categories?: { key: string; title: string; evidence: Evidence[] }[] };
export function geographicCodes(value: string): string[] {
  const codes = [...new Set(value.split(',').map(v => v.trim().toUpperCase()).filter(Boolean))];
  if (codes.some(code => !/^[A-Z]{2}$/.test(code))) throw new Error('Use comma-separated two-letter country codes.');
  return codes;
}
export function sourceUrl(value: string): string | undefined {
  try { const url = new URL(value); return url.protocol === 'https:' && url.hostname.endsWith('.wikivoyage.org') ? url.href : undefined; } catch { return undefined; }
}
export async function guideRequest<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const base = process.env.NEXT_PUBLIC_API_URL;
  if (!base) throw new Error('Guide search is not configured. Please try again later.');
  let response: Response;
  try {
    response = await fetch(`${base.replace(/\/$/, '')}/api/v1/discovery/${path}`, {
      method: body ? 'POST' : 'GET', headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined, cache: 'no-store',
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(120000)]) : AbortSignal.timeout(120000),
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error('Guide search could not be reached. Your preferences are still here; please try again.');
  }
  if (!response.ok) throw new Error(response.status === 404 ? 'This destination has no indexed guide knowledge.' : response.status === 422 ? 'Please check the search preferences and geographic codes.' : 'Guide search is temporarily unavailable. Please try again later.');
  return response.json() as Promise<T>;
}
export function discoveryBody(profile: TravelerProfile, destination?: Destination, countries: string[] = [], continents: string[] = []) {
  return destination ? { profile, destination_id: destination.geoname_id, limit: 24 } : { profile, countries, continents, limit: 8 };
}
