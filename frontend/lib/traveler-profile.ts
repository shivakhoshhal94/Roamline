// Mirrors the canonical Pydantic profile. The backend remains authoritative.
export type PreferenceLevel = "low" | "moderate" | "high";
export type TravelStyle = "relaxed" | "balanced" | "active";
export type WeatherPreference = "cool" | "mild" | "warm" | "flexible";
export type WeatherDislike = "strong_heat" | "cold" | "frequent_rain";
export type CrowdPreference = "quieter" | "neutral" | "busy_is_fine";
export type TripPace = "fewer_places" | "balanced" | "see_more";
export type ComfortRequirement =
  | "avoid_many_stairs"
  | "avoid_steep_walking"
  | "frequent_rest"
  | "wheelchair_access";
export type Interest = { name: string; importance: PreferenceLevel };
export type TravelerProfile = {
  travel_context: {
    origin_city: string | null;
    origin_country: string | null;
    start_date: string | null;
    end_date: string | null;
    duration_days: number | null;
    flexible_dates: boolean;
  };
  interests: Interest[];
  travel_style: TravelStyle | null;
  activity_level: PreferenceLevel | null;
  walking_preference: PreferenceLevel | null;
  weather_preference: WeatherPreference | null;
  weather_dislikes: WeatherDislike[];
  crowd_preference: CrowdPreference | null;
  trip_pace: TripPace | null;
  accessibility: {
    requirements: ComfortRequirement[];
    additional_requirements: string | null;
  };
};
export type ValidatedTravelerProfile = TravelerProfile & {
  travel_context: TravelerProfile["travel_context"] & { duration_days: number };
};

export const suggestedInterests = [
  "architecture",
  "history",
  "photography",
  "food",
  "nature",
  "hiking",
  "sports",
  "football",
  "art",
  "museums",
  "beaches",
  "music",
  "shopping",
  "local culture",
  "nightlife",
];
export const levels: [PreferenceLevel, string][] = [
  ["low", "Low"],
  ["moderate", "Moderate"],
  ["high", "High"],
];
export const styles: [TravelStyle, string][] = [
  ["relaxed", "Relaxed"],
  ["balanced", "Balanced"],
  ["active", "Active"],
];
export const weatherOptions: [WeatherPreference, string][] = [
  ["cool", "Cool"],
  ["mild", "Mild"],
  ["warm", "Warm"],
  ["flexible", "Flexible"],
];
export const weatherDislikes: [WeatherDislike, string][] = [
  ["strong_heat", "Strong heat"],
  ["cold", "Cold"],
  ["frequent_rain", "Frequent rain"],
];
export const crowds: [CrowdPreference, string][] = [
  ["quieter", "Quieter places"],
  ["neutral", "Neutral"],
  ["busy_is_fine", "Busy is fine"],
];
export const paces: [TripPace, string][] = [
  ["fewer_places", "Fewer places, more time"],
  ["balanced", "Balanced"],
  ["see_more", "See as much as possible"],
];
export const comforts: [ComfortRequirement, string][] = [
  ["avoid_many_stairs", "Avoid many stairs"],
  ["avoid_steep_walking", "Avoid steep walking"],
  ["frequent_rest", "Frequent rest preferred"],
  ["wheelchair_access", "Wheelchair accessibility required"],
];
export const steps = [
  "Your trip",
  "Your interests",
  "Your rhythm",
  "Your comfort",
];

export function emptyProfile(): TravelerProfile {
  return {
    travel_context: {
      origin_city: null,
      origin_country: null,
      start_date: null,
      end_date: null,
      duration_days: 7,
      flexible_dates: false,
    },
    interests: [],
    travel_style: null,
    activity_level: null,
    walking_preference: null,
    weather_preference: null,
    weather_dislikes: [],
    crowd_preference: null,
    trip_pace: null,
    accessibility: { requirements: [], additional_requirements: null },
  };
}

export type FieldErrors = Record<string, string>;
const hasControls = (value: string): boolean =>
  Array.from(value).some(
    (char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127,
  );
export function normalizeInterest(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}
export function dateDuration(
  start: string | null,
  end: string | null,
): number | null {
  if (!start || !end) return null;
  const difference = (Date.parse(end) - Date.parse(start)) / 86400000;
  return Number.isFinite(difference) ? difference + 1 : null;
}
export function validateProfile(profile: TravelerProfile): FieldErrors {
  const errors: FieldErrors = {};
  const context = profile.travel_context;
  const days = dateDuration(context.start_date, context.end_date);
  if (days !== null && days < 1)
    errors["travel_context.end_date"] =
      "End date must be on or after start date.";
  if (days !== null && days > 365)
    errors["travel_context.end_date"] = "Choose a trip of 1 to 365 days.";
  if (
    !Number.isInteger(context.duration_days) ||
    (context.duration_days ?? 0) < 1 ||
    (context.duration_days ?? 0) > 365
  )
    errors["travel_context.duration_days"] =
      "Enter a whole number from 1 to 365.";
  if (days !== null && days > 0 && context.duration_days !== days)
    errors["travel_context.duration_days"] =
      "Duration must match your dates, including arrival and departure days.";
  for (const field of ["origin_city", "origin_country"] as const) {
    const text = context[field] ?? "";
    if (text.trim().length > 80 || hasControls(text))
      errors[`travel_context.${field}`] =
        "Use plain text of up to 80 characters.";
  }
  if (profile.interests.length > 30)
    errors.interests = "Choose up to 30 interests.";
  const names = profile.interests.map((item) => normalizeInterest(item.name));
  if (new Set(names).size !== names.length)
    errors.interests = "Choose each interest only once.";
  if (
    profile.interests.some(
      (item) =>
        !normalizeInterest(item.name) ||
        normalizeInterest(item.name).length > 80 ||
        hasControls(item.name),
    )
  )
    errors.interests =
      "Interest names must be plain text of 1 to 80 characters.";
  const note = profile.accessibility.additional_requirements ?? "";
  if (note.trim().length > 500 || hasControls(note))
    errors["accessibility.additional_requirements"] =
      "Use a single line of plain text, up to 500 characters.";
  return errors;
}

export function stepForField(field: string): number {
  if (field.startsWith("travel_context")) return 0;
  if (field.startsWith("interests")) return 1;
  if (field.startsWith("weather") || field.startsWith("accessibility"))
    return 3;
  return 2;
}
