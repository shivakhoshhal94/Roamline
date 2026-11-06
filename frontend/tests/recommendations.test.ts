import test from "node:test";
import assert from "node:assert/strict";
import { api } from "../lib/api.ts";
import { emptyProfile } from "../lib/traveler-profile.ts";
import { countLabel, evidenceDate } from "../lib/recommendations.ts";

function configure(context: { after: (callback: () => void) => void }) {
  const old = process.env.NEXT_PUBLIC_API_URL;
  process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000/";
  context.after(() => {
    if (old === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = old;
  });
}

test("recommendation request uses the canonical profile and returns real result metadata", async (context) => {
  configure(context);
  const profile = emptyProfile();
  const result = {
    metadata: { status: "ready", supported_interests: ["museums"] },
    results: [
      {
        destination: { city: "Test city" },
        match_score: 42.5,
        components: [{ feature: "museums", contribution: 42.5 }],
      },
    ],
    unsupported_preferences: [],
    countries: [],
  };
  context.mock.method(
    globalThis,
    "fetch",
    async (url: string, options: RequestInit) => {
      assert.equal(
        url,
        "http://localhost:8000/api/v1/recommendations/destinations",
      );
      assert.equal(options.method, "POST");
      assert.deepEqual(JSON.parse(options.body as string), {
        profile,
        limit: 5,
      });
      return Response.json(result);
    },
  );
  const response = await api.recommendDestinations(profile);
  assert.equal(response.results[0].match_score, 42.5);
  assert.equal(response.results[0].components[0].feature, "museums");
});

test("unavailable catalog is preserved as an honest empty response", async (context) => {
  configure(context);
  context.mock.method(globalThis, "fetch", async () =>
    Response.json({
      metadata: { status: "catalog_empty" },
      results: [],
      unsupported_preferences: [],
      countries: [],
    }),
  );
  const result = await api.recommendDestinations(emptyProfile());
  assert.equal(result.metadata.status, "catalog_empty");
  assert.equal(result.results.length, 0);
});

test("recommendation service failures surface useful errors", async (context) => {
  configure(context);
  context.mock.method(
    globalThis,
    "fetch",
    async () => new Response(null, { status: 503 }),
  );
  await assert.rejects(
    api.recommendDestinations(emptyProfile()),
    /temporarily unavailable/,
  );
});

test("recommendation network failure is retryable", async (context) => {
  configure(context);
  context.mock.method(globalThis, "fetch", async () => {
    throw new Error("private network error");
  });
  await assert.rejects(
    api.recommendDestinations(emptyProfile()),
    /could not reach/,
  );
});

test("raw capped counts and freshness are presented honestly", () => {
  assert.equal(countLabel(100, true), "At least 100 mapped places");
  assert.equal(countLabel(1, false), "1 mapped place");
  assert.equal(evidenceDate("2026-11-05T23:00:00Z"), "5 Nov 2026");
});
