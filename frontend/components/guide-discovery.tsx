"use client";
import { useEffect, useRef, useState } from 'react';
import { DestinationImage } from "./destination-image";
import Link from 'next/link';
import { discoveryBody, geographicCodes, guideRequest, sourceUrl } from '@/lib/discovery';
import type { Destination, DiscoveryResponse, Evidence } from '@/lib/discovery';
import type { ValidatedTravelerProfile } from '@/lib/traveler-profile';

function Passage({ evidence }: { evidence: Evidence }) {
  const url = sourceUrl(evidence.url);
  return <blockquote className="guide-passage"><p>{evidence.snippet}</p><footer>
    {url ? <a href={url} target="_blank" rel="noreferrer">{evidence.title} · {evidence.section} ↗</a> : <span>{evidence.title} · {evidence.section}</span>}
    <small>{evidence.source} · {evidence.license} · revision {evidence.revision_id} · indexed {evidence.ingested_at.slice(0, 10)}</small>
  </footer></blockquote>;
}
export function GuideDiscovery({ profile, destination }: { profile: ValidatedTravelerProfile; destination?: Destination }) {
  const [data, setData] = useState<DiscoveryResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [countries, setCountries] = useState('');
  const [continent, setContinent] = useState('');
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);
  async function search() {
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setError(''); setData(null);
    try {
      const codes = geographicCodes(countries); setBusy(true);
      const result = await guideRequest<DiscoveryResponse>(destination ? 'destination' : 'global', discoveryBody(profile, destination, codes, continent ? [continent] : []), controller.signal);
      if (!controller.signal.aborted) setData(result);
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Search failed.'); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="guide-discovery" aria-labelledby="guide-heading">
    <p className="eyebrow">Explore with evidence</p>
    <h2 id="guide-heading">{destination ? `Find your interests in ${destination.city}` : 'Where could your curiosity take you?'}</h2>
    <p>{destination ? `Guide knowledge for your ${profile.travel_context.duration_days}-day trip. Explore ideas before planning your days.` : 'Search worldwide travel guides for places connected to your interests.'} These are retrieved passages, not a personal-match score or an itinerary.</p>
    {!destination && <div className="input-grid guide-filters"><div className="input-field"><label htmlFor="country-filter">Countries (optional)</label><input id="country-filter" placeholder="e.g. IT, JP" disabled={busy} value={countries} onChange={e => setCountries(e.target.value)} /><small>Two-letter country codes, separated by commas.</small></div>
    <div className="input-field"><label htmlFor="continent-filter">Continent</label><select id="continent-filter" disabled={busy} value={continent} onChange={e => setContinent(e.target.value)}><option value="">Worldwide</option>{[['AF','Africa'],['AS','Asia'],['EU','Europe'],['NA','North America'],['SA','South America'],['OC','Oceania']].map(([v,l]) => <option value={v} key={v}>{l}</option>)}</select></div></div>}
    <button className="primary-button" disabled={busy} onClick={search}>{busy ? 'Searching guide knowledge…' : destination ? 'Explore this destination' : 'Discover destinations'}</button>
    <p role="status" aria-live="polite">{busy ? 'Searching the local knowledge index. The first search may take a little longer.' : ''}</p>
    {error && <p role="alert" className="form-alert">{error}</p>}
    {data && <div className="guide-results"><p className="eyebrow">{destination ? destination.city : 'Your discovery shortlist'}</p>
      {data.results?.length === 0 && <p>No destinations found in this scope. Try different interests or a wider geographic area.</p>}
      {data.results?.map(item => <article className="guide-card" key={item.geoname_id}>
        <DestinationImage destinationId={item.geoname_id} /><div className="guide-card-heading"><span className="guide-rank">{String(item.rank).padStart(2,'0')}</span><div><h3>{item.city}</h3><p>{item.country} · {item.continent}</p></div></div>
        <p>{item.reasons[0]}</p>{item.evidence.map(e => <Passage key={e.chunk_id} evidence={e} />)}
        <details><summary>Why this place appeared</summary><p>{item.reasons[1]}</p><p>Semantic rank: {item.dense_rank ?? 'not in pool'} · Text-search rank: {item.lexical_rank ?? 'not in pool'}. Combined with reciprocal rank fusion, not a percentage.</p></details>
        <Link className="text-link" href={`/plan?destination=${item.geoname_id}&city=${encodeURIComponent(item.city)}`}>Explore what to visit in {item.city} →</Link>
      </article>)}
      {data.categories && <><DestinationImage destinationId={destination?.geoname_id ?? 0} /><p>Sections are grouped by deterministic section and keyword rules. Empty categories mean no supporting passages were returned.</p>{data.categories.every(c => !c.evidence.length) && <p>No supporting passages found. Try other interests.</p>}<div className="guide-category-grid">{data.categories.map(category => <section className="guide-card" key={category.key}><h3>{category.title}</h3>{category.evidence.length ? category.evidence.map(e => <Passage key={e.chunk_id} evidence={e} />) : <p className="field-hint">No passages returned for this category.</p>}</section>)}</div></>}
      <details className="guide-disclosure"><summary>Sources, search method and limitations</summary><p>Query: {data.query}</p><p>{data.method}. {data.preference_handling}</p><ul>{data.limitations.map(note => <li key={note}>{note}</li>)}</ul></details>
    </div>}
  </section>;
}
