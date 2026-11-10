import test from 'node:test';
import assert from 'node:assert/strict';
import { geographicCodes, sourceUrl, discoveryBody, guideRequest } from '../lib/discovery.ts';
import { emptyProfile as makeProfile } from '../lib/traveler-profile.ts';
const emptyProfile = makeProfile();
test('geographic constraints are explicit and normalized', () => {
  assert.deepEqual(geographicCodes(' it, JP,it '), ['IT','JP']);
  assert.deepEqual(geographicCodes(''), []);
  assert.throws(() => geographicCodes('Italy'));
});
test('source links only allow HTTPS Wikivoyage', () => {
  assert.ok(sourceUrl('https://en.wikivoyage.org/w/index.php?oldid=5'));
  assert.equal(sourceUrl('javascript:alert(1)'), undefined);
  assert.equal(sourceUrl('https://en.wikivoyage.org.attacker.com'), undefined);
});
test('both journeys reuse the original traveler contract without a score', () => {
  assert.deepEqual(discoveryBody(emptyProfile), {profile: emptyProfile,countries:[],continents:[],limit:8});
  const city={geoname_id:1,city:'Rome',country:'Italy',country_code:'IT',continent:'EU'};
  assert.deepEqual(discoveryBody(emptyProfile, city), {profile:emptyProfile,destination_id:1,limit:24});
});
test('real API boundary handles error and empty response', async () => {
  const oldFetch=globalThis.fetch; const base=process.env.NEXT_PUBLIC_API_URL;
  process.env.NEXT_PUBLIC_API_URL='http://localhost:8000';
  try {
    globalThis.fetch=async (input, init) => {
      assert.equal(input,'http://localhost:8000/api/v1/discovery/global');
      assert.equal(init?.method,'POST');
      return new Response(JSON.stringify({results:[]}),{status:200});
    };
    assert.deepEqual(await guideRequest('global',discoveryBody(emptyProfile)),{results:[]});
    globalThis.fetch=async () => new Response('',{status:503});
    await assert.rejects(guideRequest('global',{}),/temporarily unavailable/);
  } finally { globalThis.fetch=oldFetch; if (base===undefined) delete process.env.NEXT_PUBLIC_API_URL; else process.env.NEXT_PUBLIC_API_URL=base; }
});
