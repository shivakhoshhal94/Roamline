export type UnsupportedPreference = { preference: string; reason: string };
export type ScoreComponent = {
  feature: string;
  user_importance: "low" | "moderate" | "high";
  weight: number;
  destination_feature: number;
  contribution: number;
  raw_count: number;
  capped: boolean;
  categories: string[];
  proxy: string;
};
export type DestinationMatch = {
  destination: {
    geoname_id: number;
    city: string;
    country_code: string;
    country: string;
    latitude: number;
    longitude: number;
    population: number;
    timezone: string | null;
  };
  match_score: number;
  components: ScoreComponent[];
  top_reasons: string[];
  data: {
    provider: string;
    place_id: string;
    fetched_at: string;
    stale: boolean;
    mapping_version: string;
    normalization_version: string;
    request_strategy: string;
    category_query_limit: number;
    unique_poi_count: number;
    catalog_source: string;
    catalog_source_sha256: string;
    country_info_sha256: string;
    catalog_imported_at: string;
  };
};
export type DestinationRecommendations = {
  metadata: {
    status:
      | "ready"
      | "catalog_empty"
      | "no_enriched_candidates"
      | "no_supported_interests";
    message: string;
    candidate_policy: string;
    candidate_min_population: number;
    candidate_max_count: number;
    candidate_count: number;
    evaluated_count: number;
    excluded_incomplete_count: number;
    supported_interests: string[];
    generated_at: string;
    scoring_version: string;
    score_scale: string;
    tie_break: string;
    attribution: string[];
  };
  results: DestinationMatch[];
  unsupported_preferences: UnsupportedPreference[];
  countries: {
    country_code: string;
    country: string;
    destination_ids: number[];
  }[];
};

export function evidenceDate(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(value));
}
export function countLabel(count: number, capped: boolean): string {
  return `${capped ? "At least " : ""}${count} mapped ${count === 1 ? "place" : "places"}`;
}
