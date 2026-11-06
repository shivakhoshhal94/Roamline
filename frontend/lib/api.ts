import type { DestinationRecommendations } from "./recommendations.ts";
import type {
  TravelerProfile,
  ValidatedTravelerProfile,
  FieldErrors,
} from "./traveler-profile.ts";

export type HealthResponse = { status: "ok" };
export type ReadyResponse = { status: "ready" };

async function getStatus<T extends "ok" | "ready">(
  path: string,
  expected: T,
): Promise<{ status: T }> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL;
  if (!baseUrl)
    throw new Error("NEXT_PUBLIC_API_URL is required for API requests");
  const response = await fetch(`${baseUrl.replace(/\/$/, "")}${path}`, {
    cache: "no-store",
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`API request failed (${response.status})`);
  const data: unknown = await response.json();
  if (
    typeof data !== "object" ||
    data === null ||
    !("status" in data) ||
    data.status !== expected
  ) {
    throw new Error("Unexpected API status response");
  }
  return { status: expected };
}

export class ProfileValidationError extends Error {
  readonly fields: FieldErrors;
  constructor(fields: FieldErrors) {
    super("Please check your travel preferences.");
    this.name = "ProfileValidationError";
    this.fields = fields;
  }
}

async function validateTravelerProfile(
  profile: TravelerProfile,
): Promise<ValidatedTravelerProfile> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL;
  if (!baseUrl)
    throw new Error(
      "The travel preference service is not configured. Please try again later.",
    );
  let response: Response;
  try {
    response = await fetch(
      `${baseUrl.replace(/\/$/, "")}/api/v1/traveler-profile/validate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(profile),
        signal: AbortSignal.timeout(10000),
        cache: "no-store",
      },
    );
  } catch {
    throw new Error(
      "We could not reach the preference service. Your entries are still here; please try again.",
    );
  }
  if (response.status === 422) {
    const data = await response.json();
    const fields: FieldErrors = {};
    if (Array.isArray(data.detail)) {
      for (const issue of data.detail) {
        const path = Array.isArray(issue.loc)
          ? issue.loc.slice(1).join(".")
          : "form";
        const field =
          path === "travel_context"
            ? "travel_context.end_date"
            : path.startsWith("interests")
              ? "interests"
              : path === "accessibility"
                ? "accessibility.additional_requirements"
                : path || "form";
        fields[field] =
          typeof issue.msg === "string"
            ? issue.msg.replace(/^Value error, /, "")
            : "Please check this value.";
      }
    }
    throw new ProfileValidationError(
      Object.keys(fields).length
        ? fields
        : { form: "Please check your preferences." },
    );
  }
  if (!response.ok)
    throw new Error(
      "The preference service is temporarily unavailable. Please try again.",
    );
  return response.json() as Promise<ValidatedTravelerProfile>;
}

async function recommendDestinations(
  profile: TravelerProfile,
  limit = 5,
): Promise<DestinationRecommendations> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL;
  if (!baseUrl)
    throw new Error(
      "The destination service is not configured. Please try again later.",
    );
  let response: Response;
  try {
    response = await fetch(
      `${baseUrl.replace(/\/$/, "")}/api/v1/recommendations/destinations`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile, limit }),
        cache: "no-store",
        signal: AbortSignal.timeout(10000),
      },
    );
  } catch {
    throw new Error(
      "We could not reach the destination service. Please try again.",
    );
  }
  if (response.status === 422)
    throw new Error("Please check your preferences and try again.");
  if (!response.ok)
    throw new Error(
      "Destination data is temporarily unavailable. Please try again later.",
    );
  return response.json() as Promise<DestinationRecommendations>;
}

export const api = {
  recommendDestinations,
  validateTravelerProfile,
  health: (): Promise<HealthResponse> => getStatus("/health", "ok"),
  ready: (): Promise<ReadyResponse> => getStatus("/ready", "ready"),
};
