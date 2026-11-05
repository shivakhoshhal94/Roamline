import test from "node:test";
import assert from "node:assert/strict";
import {
  dateDuration,
  emptyProfile,
  normalizeInterest,
  stepForField,
  validateProfile,
} from "../lib/traveler-profile.ts";
import { api, ProfileValidationError } from "../lib/api.ts";

test("inclusive dates handle leap days and match duration validation", () => {
  assert.equal(dateDuration("2028-02-28", "2028-03-01"), 3);
  const profile = emptyProfile();
  profile.travel_context.start_date = "2028-02-28";
  profile.travel_context.end_date = "2028-03-01";
  assert.ok(validateProfile(profile)["travel_context.duration_days"]);
  profile.travel_context.duration_days = 3;
  assert.deepEqual(validateProfile(profile), {});
  profile.travel_context.end_date = "2028-02-27";
  assert.ok(validateProfile(profile)["travel_context.end_date"]);
});

test("duration must be a whole number within bounds", () => {
  for (const days of [null, 0, -2, 1.5, 366]) {
    const profile = emptyProfile();
    profile.travel_context.duration_days = days;
    assert.ok(validateProfile(profile)["travel_context.duration_days"]);
  }
});

test("custom interests normalize, reject duplicates and malformed text", () => {
  assert.equal(
    normalizeInterest("  Street   Photography  "),
    "street photography",
  );
  const profile = emptyProfile();
  profile.interests = [
    { name: "Food", importance: "high" },
    { name: " food ", importance: "low" },
  ];
  assert.ok(validateProfile(profile).interests);
  profile.interests = [{ name: "bad\x00name", importance: "high" }];
  assert.ok(validateProfile(profile).interests);
});

test("accessibility and origin text limits route errors to the relevant stage", () => {
  const profile = emptyProfile();
  profile.accessibility.additional_requirements = "a".repeat(501);
  profile.travel_context.origin_city = "a".repeat(81);
  const errors = validateProfile(profile);
  assert.ok(errors["accessibility.additional_requirements"]);
  assert.ok(errors["travel_context.origin_city"]);
  assert.equal(stepForField("accessibility.additional_requirements"), 3);
  assert.equal(stepForField("interests.0.name"), 1);
});

test("typed API posts the profile and returns the canonical response", async (context) => {
  const oldUrl = process.env.NEXT_PUBLIC_API_URL;
  process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000/";
  context.after(() => {
    if (oldUrl === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = oldUrl;
  });
  const profile = emptyProfile();
  const canonical = {
    ...profile,
    travel_context: { ...profile.travel_context, origin_city: "Rome" },
  };
  context.mock.method(
    globalThis,
    "fetch",
    async (url: string, options: RequestInit) => {
      assert.equal(
        url,
        "http://localhost:8000/api/v1/traveler-profile/validate",
      );
      assert.equal(options.method, "POST");
      assert.deepEqual(JSON.parse(options.body as string), profile);
      return Response.json(canonical);
    },
  );
  assert.deepEqual(await api.validateTravelerProfile(profile), canonical);
});

test("API 422 field errors stay useful to the form", async (context) => {
  process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000";
  context.mock.method(globalThis, "fetch", async () =>
    Response.json(
      {
        detail: [
          {
            loc: ["body", "interests", 0, "importance"],
            msg: "Choose low, moderate or high",
          },
        ],
      },
      { status: 422 },
    ),
  );
  await assert.rejects(
    api.validateTravelerProfile(emptyProfile()),
    (error: unknown) =>
      error instanceof ProfileValidationError && !!error.fields.interests,
  );
});

test("network failures keep a retryable message", async (context) => {
  process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000";
  context.mock.method(globalThis, "fetch", async () => {
    throw new Error("network failure");
  });
  await assert.rejects(
    api.validateTravelerProfile(emptyProfile()),
    /entries are still here/,
  );
});
