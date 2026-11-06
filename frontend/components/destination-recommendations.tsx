"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ValidatedTravelerProfile } from "@/lib/traveler-profile";
import type { DestinationRecommendations as RecommendationResponse } from "@/lib/recommendations";
import { countLabel, evidenceDate } from "@/lib/recommendations";

export function DestinationRecommendations({
  profile,
  onEdit,
}: {
  profile: ValidatedTravelerProfile;
  onEdit: () => void;
}) {
  const [data, setData] = useState<RecommendationResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const resultHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (data) resultHeading.current?.focus();
  }, [data]);

  async function findMatches() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setData(await api.recommendDestinations(profile, 5));
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Destination matches are unavailable. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="destination-discovery"
      aria-labelledby="destination-intro"
    >
      <p className="eyebrow">Your interests, a new direction</p>
      <h2 id="destination-intro">Where could they take you?</h2>
      <p>
        Explore cities using mapped-place evidence for your interests. These
        matches do not assess weather, crowds, costs, walking conditions or
        accessibility.
      </p>
      <button
        type="button"
        className="primary-button"
        disabled={busy}
        onClick={findMatches}
      >
        {busy
          ? "Finding matches..."
          : data
            ? "Find matches again"
            : "Find destination matches"}
      </button>
      <p className="submission-status" role="status">
        {busy
          ? "Comparing your supported interests with prepared destination evidence."
          : ""}
      </p>
      {error && (
        <div role="alert" className="form-alert">
          {error} Your preferences are still here.
        </div>
      )}
      {data && (
        <div className="destination-results" aria-busy={busy}>
          <h2 ref={resultHeading} tabIndex={-1}>
            {data.metadata.status === "ready"
              ? "Places for your interests."
              : "A little more preparation is needed."}
          </h2>
          <p>{data.metadata.message}</p>
          {data.metadata.status === "no_supported_interests" && (
            <button type="button" className="secondary-button" onClick={onEdit}>
              Choose other interests
            </button>
          )}
          {data.unsupported_preferences.length > 0 && (
            <details className="ranking-limits">
              <summary>What was not included in these matches</summary>
              <p>
                Unsupported interests and other travel preferences never
                silently contribute to the score.
              </p>
              <ul>
                {data.unsupported_preferences.map((item) => (
                  <li key={item.preference}>
                    <strong>{item.preference}:</strong> {item.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {data.results.length > 0 && (
            <>
              <p className="result-count">
                {data.results.length}{" "}
                {data.results.length === 1 ? "city" : "cities"} across{" "}
                {data.countries.length}{" "}
                {data.countries.length === 1 ? "country" : "countries"}.
                Compared with {data.metadata.evaluated_count} prepared
                candidates.
              </p>
              <div className="destination-grid">
                {data.results.map((match) => (
                  <article
                    className="destination-card"
                    key={match.destination.geoname_id}
                  >
                    <header>
                      <div>
                        <p className="eyebrow">{match.destination.country}</p>
                        <h3>{match.destination.city}</h3>
                      </div>
                      <div className="evidence-score">
                        <strong>
                          {match.match_score.toFixed(1)}
                          <span> / 100</span>
                        </strong>
                        <span>Evidence match</span>
                      </div>
                    </header>
                    <ul className="match-reasons">
                      {match.top_reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                    <p className="data-date">
                      Evidence checked {evidenceDate(match.data.fetched_at)}
                      {match.data.stale && (
                        <strong> · Older evidence; refresh advised</strong>
                      )}
                    </p>
                    <details className="score-details">
                      <summary>How this match was calculated</summary>
                      <p>
                        A weighted average of supported interest signals. Higher
                        importance means more influence. This is not a quality
                        rating or a probability.
                      </p>
                      <div className="score-table-wrap">
                        <table>
                          <caption>
                            Interest contributions for {match.destination.city}
                          </caption>
                          <thead>
                            <tr>
                              <th scope="col">Interest</th>
                              <th scope="col">Importance</th>
                              <th scope="col">Signal</th>
                              <th scope="col">Points</th>
                            </tr>
                          </thead>
                          <tbody>
                            {match.components.map((component) => (
                              <tr key={component.feature}>
                                <th scope="row">{component.feature}</th>
                                <td>{component.user_importance}</td>
                                <td>
                                  {component.destination_feature.toFixed(3)}
                                </td>
                                <td>{component.contribution.toFixed(1)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <ul className="feature-evidence">
                        {match.components.map((component) => (
                          <li key={component.feature}>
                            <strong>{component.feature}:</strong>{" "}
                            {countLabel(component.raw_count, component.capped)}.{" "}
                            {component.proxy}
                            <small>
                              Categories: {component.categories.join(", ")}.
                              Weight: {component.weight.toFixed(3)}.
                            </small>
                          </li>
                        ))}
                      </ul>
                      <p className="field-hint">
                        Counts are capped and log-transformed, with a
                        population-adjusted signal. Rounded contributions may
                        not sum exactly to the displayed score. Overlapping
                        feature categories may reflect some of the same places.
                      </p>
                      <p className="field-hint">
                        Category samples: up to{" "}
                        {match.data.category_query_limit} places each. Strategy:{" "}
                        {match.data.request_strategy}. Unique sampled POIs:{" "}
                        {match.data.unique_poi_count}. Mapping:{" "}
                        {match.data.mapping_version}. Normalization:{" "}
                        {match.data.normalization_version}. Scoring:{" "}
                        {data.metadata.scoring_version}. GeoNames ID:{" "}
                        {match.destination.geoname_id}.
                      </p>
                    </details>
                  </article>
                ))}
              </div>
              <p className="ranking-caveat">
                Mapped places do not measure experience quality. Coverage varies
                by region, and the bounded city catalog can miss smaller
                destinations. Check practical suitability before making travel
                plans.
              </p>
            </>
          )}
          <p className="data-attribution">
            City catalog: <a href="https://www.geonames.org/">GeoNames</a> (
            <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>
            ). Place evidence: <a href="https://www.geoapify.com/">Geoapify</a>,
            ©{" "}
            <a href="https://www.openstreetmap.org/copyright">
              OpenStreetMap contributors
            </a>{" "}
            (ODbL).
          </p>
        </div>
      )}
    </section>
  );
}
