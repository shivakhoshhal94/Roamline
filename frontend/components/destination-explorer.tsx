"use client";
import { useState } from 'react';
import { guideRequest } from '@/lib/discovery';
import type { Destination } from '@/lib/discovery';
import { TravelerProfileForm } from './traveler-profile-form';
export function DestinationExplorer({ initialCity = "" }: { initialCity?: string }) {
  const [query, setQuery] = useState(initialCity);
  const [results, setResults] = useState<Destination[]>([]);
  const [selected, setSelected] = useState<Destination | undefined>();
  const [busy, setBusy] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState('');
  async function search() {
    setBusy(true); setError(''); setResults([]); setSearched(false);
    try { const data = await guideRequest<{results: Destination[]}>(`destinations?q=${encodeURIComponent(query.trim())}`); setResults(data.results); setSearched(true); }
    catch(e) { setError(e instanceof Error ? e.message : 'Search unavailable.'); }
    finally { setBusy(false); }
  }
  if (selected) return <><div className="destination-context"><p>Exploring <strong>{selected.city}, {selected.country}</strong> · Set your trip length and preferences below.</p><button className="secondary-button" onClick={() => setSelected(undefined)}>Change destination</button></div><TravelerProfileForm key={selected.geoname_id} destination={selected} /></>;
  return <main className="intake-page destination-picker"><p className="eyebrow">A place in mind</p><h1>Make room for <em>discovery.</em></h1><p>Choose your destination, then tell us what you enjoy. Explore sourced guide knowledge before building an itinerary.</p>
    <form onSubmit={e => {e.preventDefault(); void search();}}><div className="input-field"><label htmlFor="destination-search">Which city are you visiting?</label><input id="destination-search" required minLength={2} maxLength={80} value={query} onChange={e => setQuery(e.target.value)} placeholder="Search a city" /></div><button className="primary-button" disabled={busy}>{busy ? 'Looking up cities…' : 'Find destination'}</button></form>
    <p role="status">{busy ? 'Searching indexed destinations.' : searched && !results.length ? 'No indexed destinations found. Try another city name.' : ''}</p>{error && <p role="alert">{error}</p>}
    <ul className="destination-options">{results.map(city => <li key={city.geoname_id}><button className="secondary-button" onClick={() => setSelected(city)}>{city.city} · {city.country} <span>Explore →</span></button></li>)}</ul>
    <p className="privacy-note">No itinerary, live availability or opening-hours verification yet. Guide coverage varies by destination.</p>
  </main>;
}
