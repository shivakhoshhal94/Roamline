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

export const api = {
  health: (): Promise<HealthResponse> => getStatus("/health", "ok"),
  ready: (): Promise<ReadyResponse> => getStatus("/ready", "ready"),
};
