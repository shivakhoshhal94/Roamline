import { GuideDiscovery } from "./guide-discovery";
import type { Destination } from "@/lib/discovery";
import type { ValidatedTravelerProfile } from "@/lib/traveler-profile";
import {
  comforts,
  crowds,
  levels,
  paces,
  styles,
  weatherDislikes,
  weatherOptions,
} from "@/lib/traveler-profile";

function label(
  options: readonly (readonly [string, string])[],
  value: string | null,
): string {
  return options.find(([key]) => key === value)?.[1] ?? "No preference";
}
function displayDate(value: string | null): string {
  return value
    ? new Intl.DateTimeFormat("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
        timeZone: "UTC",
      }).format(new Date(`${value}T00:00:00Z`))
    : "Not set";
}
export function ProfileSummary({
  profile,
  onEdit,
  destination,
}: {
  profile: ValidatedTravelerProfile;
  onEdit: () => void;
  destination?: Destination;
}) {
  const trip = profile.travel_context;
  return (
    <section className="profile-summary" aria-labelledby="summary-title">
      <p className="eyebrow">A good place to begin</p>
      <h1 id="summary-title" tabIndex={-1}>
        Your travel preferences are ready.
      </h1>
      <p className="subtitle">
        Validated and shaped around you. Explore destination matches from your
        interests and explore guide knowledge for a place you already have in mind.
      </p>
      <div className="summary-grid">
        <section>
          <h2>Your trip</h2>
          <dl>
            <dt>Starting from</dt>
            <dd>
              {[trip.origin_city, trip.origin_country]
                .filter(Boolean)
                .join(", ") || "Not set"}
            </dd>
            <dt>Dates</dt>
            <dd>
              {displayDate(trip.start_date)} → {displayDate(trip.end_date)}
            </dd>
            <dt>Duration</dt>
            <dd>
              {trip.duration_days} {trip.duration_days === 1 ? "day" : "days"} ·{" "}
              {trip.flexible_dates
                ? "Flexible dates"
                : "Dates not marked flexible"}
            </dd>
          </dl>
        </section>
        <section>
          <h2>What draws you in</h2>
          {profile.interests.length ? (
            <ul className="summary-interests">
              {profile.interests.map((interest) => (
                <li key={interest.name}>
                  <span>{interest.name}</span>
                  <span>{label(levels, interest.importance)} importance</span>
                </li>
              ))}
            </ul>
          ) : (
            <p>No interests selected.</p>
          )}
        </section>
        <section>
          <h2>Your rhythm</h2>
          <dl>
            <dt>Travel style</dt>
            <dd>{label(styles, profile.travel_style)}</dd>
            <dt>Activity / walking</dt>
            <dd>
              {label(levels, profile.activity_level)} /{" "}
              {label(levels, profile.walking_preference)}
            </dd>
            <dt>Crowds</dt>
            <dd>{label(crowds, profile.crowd_preference)}</dd>
            <dt>Pace</dt>
            <dd>{label(paces, profile.trip_pace)}</dd>
          </dl>
        </section>
        <section>
          <h2>Your comfort</h2>
          <dl>
            <dt>Weather</dt>
            <dd>{label(weatherOptions, profile.weather_preference)}</dd>
            <dt>Prefer to avoid</dt>
            <dd>
              {profile.weather_dislikes
                .map((value) => label(weatherDislikes, value))
                .join(", ") || "Nothing specified"}
            </dd>
            <dt>Comfort and accessibility</dt>
            <dd>
              {profile.accessibility.requirements
                .map((value) => label(comforts, value))
                .join(", ") || "Nothing specified"}
            </dd>
            <dt>Additional requirements</dt>
            <dd>
              {profile.accessibility.additional_requirements ||
                "None specified"}
            </dd>
          </dl>
        </section>
      </div>
      <p className="privacy-note">
        This profile is not saved. Validated preferences stay in memory between journeys until you
        refresh. Your profile is sent for matching only when you
        request destination matches. Itinerary planning is not available yet.
      </p>
      <button className="primary-button" type="button" onClick={onEdit}>
        Edit preferences
      </button>
      <GuideDiscovery profile={profile} destination={destination} />
    </section>
  );
}
