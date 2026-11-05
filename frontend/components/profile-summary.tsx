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
}: {
  profile: ValidatedTravelerProfile;
  onEdit: () => void;
}) {
  const trip = profile.travel_context;
  return (
    <section className="profile-summary" aria-labelledby="summary-title">
      <p className="eyebrow">A good place to begin</p>
      <h1 id="summary-title" tabIndex={-1}>
        Your travel preferences are ready.
      </h1>
      <p className="subtitle">
        Validated and shaped around you. This profile will support both
        discovery and trip planning.
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
        This profile is not saved. Your entries stay in this page until you
        leave or refresh. Destination recommendations and itineraries are not
        available yet.
      </p>
      <button className="primary-button" type="button" onClick={onEdit}>
        Edit preferences
      </button>
    </section>
  );
}
