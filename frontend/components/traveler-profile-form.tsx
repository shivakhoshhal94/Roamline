"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { api, ProfileValidationError } from "@/lib/api";
import {
  comforts,
  crowds,
  dateDuration,
  emptyProfile,
  levels,
  normalizeInterest,
  paces,
  steps,
  stepForField,
  styles,
  suggestedInterests,
  validateProfile,
  weatherDislikes,
  weatherOptions,
} from "@/lib/traveler-profile";
import type {
  FieldErrors,
  Interest,
  PreferenceLevel,
  TravelerProfile,
  ValidatedTravelerProfile,
} from "@/lib/traveler-profile";
import { PreferenceChoices } from "./preference-choices";
import { ProfileSummary } from "./profile-summary";

const introductions = [
  [
    "When are you travelling?",
    "A rough idea is enough. Start with your dates or the number of days you have in mind.",
  ],
  [
    "What do you enjoy?",
    "Follow your curiosity. Pick interests and hobbies, then tell us how much each matters.",
  ],
  [
    "Find your kind of rhythm.",
    "A slow morning or a full day out? Tell us what feels right for you.",
  ],
  [
    "Make room for comfort.",
    "The weather you enjoy, and the practical things that help you feel at ease.",
  ],
];

export function TravelerProfileForm() {
  const [profile, setProfile] = useState<TravelerProfile>(emptyProfile);
  const [step, setStep] = useState(0);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [customInterest, setCustomInterest] = useState("");
  const [busy, setBusy] = useState(false);
  const [validated, setValidated] = useState<ValidatedTravelerProfile | null>(
    null,
  );
  const heading = useRef<HTMLHeadingElement>(null);
  const form = useRef<HTMLFormElement>(null);

  const focusIntent = useRef<"step" | "error" | "summary" | null>(null);
  useEffect(() => {
    const intent = focusIntent.current;
    if (!intent) return;
    const target =
      intent === "summary"
        ? document.getElementById("summary-title")
        : intent === "error"
          ? (form.current?.querySelector<HTMLElement>(
              '[aria-invalid="true"]',
            ) ?? form.current?.querySelector<HTMLElement>('[role="alert"]'))
          : heading.current;
    focusIntent.current = null;
    target?.focus();
    target?.scrollIntoView({ block: "start" });
  }, [step, errors, validated]);

  function moveTo(next: number) {
    focusIntent.current = "step";
    setStep(next);
  }
  function update<K extends keyof TravelerProfile>(
    key: K,
    value: TravelerProfile[K],
  ) {
    setProfile((previous) => ({ ...previous, [key]: value }));
    setErrors({});
  }
  function updateContext(
    key: keyof TravelerProfile["travel_context"],
    value: string | number | boolean | null,
  ) {
    setProfile((previous) => {
      const context = { ...previous.travel_context, [key]: value };
      const duration = dateDuration(context.start_date, context.end_date);
      if (duration !== null && duration > 0) context.duration_days = duration;
      return { ...previous, travel_context: context };
    });
    setErrors({});
  }
  function showErrors(nextErrors: FieldErrors) {
    setErrors(nextErrors);
    const first = Object.keys(nextErrors)[0];
    if (first !== "form")
      moveTo(first === "custom_interest" ? 1 : stepForField(first));
    focusIntent.current = "error";
  }

  function toggleInterest(name: string) {
    const exists = profile.interests.some((item) => item.name === name);
    if (!exists && profile.interests.length >= 30) {
      setErrors({ interests: "Choose up to 30 interests." });
      return;
    }
    update(
      "interests",
      exists
        ? profile.interests.filter((item) => item.name !== name)
        : [...profile.interests, { name, importance: "moderate" }],
    );
  }
  function addCustomInterest() {
    const name = normalizeInterest(customInterest);
    const candidate: Interest[] = [
      ...profile.interests,
      { name, importance: "moderate" },
    ];
    const error = validateProfile({
      ...profile,
      interests: candidate,
    }).interests;
    if (error) {
      setErrors({ custom_interest: error });
      return;
    }
    update("interests", candidate);
    setCustomInterest("");
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const allErrors = validateProfile(profile);
    if (step === 1 && customInterest.trim())
      allErrors.custom_interest =
        "Add this interest or clear the field before continuing.";
    const relevant = Object.fromEntries(
      Object.entries(allErrors).filter(
        ([field]) =>
          step === 3 ||
          (field === "custom_interest"
            ? step === 1
            : stepForField(field) === step),
      ),
    );
    if (Object.keys(relevant).length) {
      showErrors(relevant);
      return;
    }
    if (step < 3) {
      moveTo(step + 1);
      return;
    }
    setErrors({});
    setBusy(true);
    try {
      const result = await api.validateTravelerProfile(profile);
      focusIntent.current = "summary";
      setValidated(result);
    } catch (error) {
      if (error instanceof ProfileValidationError) showErrors(error.fields);
      else
        setErrors({
          form:
            error instanceof Error
              ? error.message
              : "Something went wrong. Please try again.",
        });
    } finally {
      setBusy(false);
    }
  }
  const trip = profile.travel_context;
  const durationFromDates = dateDuration(trip.start_date, trip.end_date);
  const customNames = profile.interests
    .map((item) => item.name)
    .filter((name) => !suggestedInterests.includes(name));
  const fieldError = (name: string) =>
    errors[name] ? (
      <p className="field-error" id={`${name}-error`}>
        {errors[name]}
      </p>
    ) : null;
  const errorProps = (name: string) => ({
    "aria-invalid": !!errors[name],
    "aria-describedby": errors[name] ? `${name}-error` : undefined,
  });

  return (
    <main className="intake-page">
      <Link className="text-link" href="/">
        &larr; Back to Roamline
      </Link>
      {validated ? (
        <ProfileSummary
          profile={validated}
          onEdit={() => {
            setValidated(null);
            moveTo(0);
          }}
        />
      ) : (
        <div className="intake-layout">
          <aside className="intake-sidebar">
            <p className="eyebrow">Discover &middot; Start with you</p>
            <h1>
              A trip that <br />
              feels like <em>you.</em>
            </h1>
            <p>One profile, for where to go and how to spend your days.</p>
            <nav aria-label="Preference intake progress">
              <ol className="step-list">
                {steps.map((label, index) => (
                  <li
                    key={label}
                    aria-current={step === index ? "step" : undefined}
                  >
                    <span>{String(index + 1).padStart(2, "0")}</span>
                    <span>
                      {label}
                      {index < step && <small> &middot; Complete</small>}
                    </span>
                  </li>
                ))}
              </ol>
            </nav>
            <p className="privacy-note">
              No account. No saved profile.
              <br />
              Your entries stay here until you leave or refresh.
            </p>
          </aside>
          <div className="intake-panel">
            <p className="eyebrow">
              Step {step + 1} of {steps.length}
            </p>
            <h2 ref={heading} tabIndex={-1}>
              {introductions[step][0]}
            </h2>
            <p className="step-intro">{introductions[step][1]}</p>
            <form
              ref={form}
              onSubmit={submit}
              noValidate
              aria-label="Traveler preferences"
            >
              {Object.keys(errors).length > 0 && (
                <div className="form-alert" role="alert" tabIndex={-1}>
                  {errors.form ||
                    "A few details need a second look. Check the messages below."}
                </div>
              )}
              <fieldset className="form-fields" disabled={busy}>
                {step === 0 && (
                  <>
                    <div className="input-grid">
                      <div className="input-field">
                        <label htmlFor="origin-city">
                          Origin city <span>(optional)</span>
                        </label>
                        <input
                          id="origin-city"
                          value={trip.origin_city ?? ""}
                          maxLength={80}
                          autoComplete="address-level2"
                          onChange={(event) =>
                            updateContext(
                              "origin_city",
                              event.target.value || null,
                            )
                          }
                          {...errorProps("travel_context.origin_city")}
                        />
                        {fieldError("travel_context.origin_city")}
                      </div>
                      <div className="input-field">
                        <label htmlFor="origin-country">
                          Origin country <span>(optional)</span>
                        </label>
                        <input
                          id="origin-country"
                          value={trip.origin_country ?? ""}
                          maxLength={80}
                          autoComplete="country-name"
                          onChange={(event) =>
                            updateContext(
                              "origin_country",
                              event.target.value || null,
                            )
                          }
                          {...errorProps("travel_context.origin_country")}
                        />
                        {fieldError("travel_context.origin_country")}
                      </div>
                    </div>
                    <div className="input-grid">
                      <div className="input-field">
                        <label htmlFor="start-date">
                          Start date <span>(optional)</span>
                        </label>
                        <input
                          id="start-date"
                          type="date"
                          value={trip.start_date ?? ""}
                          onChange={(event) =>
                            updateContext(
                              "start_date",
                              event.target.value || null,
                            )
                          }
                          {...errorProps("travel_context.start_date")}
                        />
                        {fieldError("travel_context.start_date")}
                      </div>
                      <div className="input-field">
                        <label htmlFor="end-date">
                          End date <span>(optional)</span>
                        </label>
                        <input
                          id="end-date"
                          type="date"
                          value={trip.end_date ?? ""}
                          onChange={(event) =>
                            updateContext(
                              "end_date",
                              event.target.value || null,
                            )
                          }
                          {...errorProps("travel_context.end_date")}
                        />
                        {fieldError("travel_context.end_date")}
                      </div>
                    </div>
                    <div className="input-field duration-field">
                      <label htmlFor="duration">Trip duration (days)</label>
                      <input
                        id="duration"
                        type="number"
                        min={1}
                        max={365}
                        step={1}
                        value={trip.duration_days ?? ""}
                        readOnly={
                          durationFromDates !== null && durationFromDates > 0
                        }
                        onChange={(event) =>
                          updateContext(
                            "duration_days",
                            event.target.value === ""
                              ? null
                              : Number(event.target.value),
                          )
                        }
                        aria-describedby={
                          errors["travel_context.duration_days"]
                            ? "travel_context.duration_days-error"
                            : "duration-hint"
                        }
                        aria-invalid={!!errors["travel_context.duration_days"]}
                      />
                      <p className="field-hint" id="duration-hint">
                        1–365 days. With both dates set, we count arrival and
                        departure days automatically.
                      </p>
                      {fieldError("travel_context.duration_days")}
                    </div>
                    <label className="check-option">
                      <input
                        type="checkbox"
                        checked={trip.flexible_dates}
                        onChange={(event) =>
                          updateContext("flexible_dates", event.target.checked)
                        }
                      />
                      <span>My dates are flexible</span>
                    </label>
                  </>
                )}
                {step === 1 && (
                  <>
                    <fieldset className="preference-group">
                      <legend>Interests and hobbies</legend>
                      <p className="field-hint">
                        Pick any that feel like you, or skip this section. Up to
                        30 interests.
                      </p>
                      <div className="interest-grid">
                        {[...suggestedInterests, ...customNames].map((name) => (
                          <label className="interest-choice" key={name}>
                            <input
                              type="checkbox"
                              checked={profile.interests.some(
                                (item) => item.name === name,
                              )}
                              onChange={() => toggleInterest(name)}
                            />
                            <span>{name}</span>
                          </label>
                        ))}
                      </div>
                      {fieldError("interests")}
                    </fieldset>
                    <div className="input-field">
                      <label htmlFor="custom-interest">
                        Something else you enjoy?
                      </label>
                      <div className="add-interest">
                        <input
                          id="custom-interest"
                          maxLength={80}
                          value={customInterest}
                          placeholder="e.g. birdwatching"
                          onChange={(event) => {
                            setCustomInterest(event.target.value);
                            setErrors({});
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.preventDefault();
                              addCustomInterest();
                            }
                          }}
                          {...errorProps("custom_interest")}
                        />
                        <button
                          className="secondary-button"
                          type="button"
                          onClick={addCustomInterest}
                        >
                          Add interest
                        </button>
                      </div>
                      {fieldError("custom_interest")}
                    </div>
                    {profile.interests.length > 0 && (
                      <div className="interest-priorities">
                        <h3>How much does each matter?</h3>
                        {profile.interests.map((interest) => (
                          <div className="priority-row" key={interest.name}>
                            <label
                              htmlFor={`importance-${encodeURIComponent(interest.name)}`}
                            >
                              {interest.name}
                            </label>
                            <select
                              id={`importance-${encodeURIComponent(interest.name)}`}
                              value={interest.importance}
                              onChange={(event) =>
                                update(
                                  "interests",
                                  profile.interests.map((item) =>
                                    item.name === interest.name
                                      ? {
                                          ...item,
                                          importance: event.target
                                            .value as PreferenceLevel,
                                        }
                                      : item,
                                  ),
                                )
                              }
                            >
                              {levels.map(([value, label]) => (
                                <option key={value} value={value}>
                                  {label} importance
                                </option>
                              ))}
                            </select>
                            <button
                              type="button"
                              className="remove-interest"
                              aria-label={`Remove ${interest.name}`}
                              onClick={() => toggleInterest(interest.name)}
                            >
                              Remove
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}
                {step === 2 && (
                  <>
                    <PreferenceChoices
                      name="travel_style"
                      legend="What kind of trip do you prefer?"
                      options={styles}
                      value={profile.travel_style}
                      onChange={(value) => update("travel_style", value)}
                      error={errors.travel_style}
                    />
                    <PreferenceChoices
                      name="activity_level"
                      legend="How active do you want to be?"
                      options={levels}
                      value={profile.activity_level}
                      onChange={(value) => update("activity_level", value)}
                      error={errors.activity_level}
                    />
                    <PreferenceChoices
                      name="walking_preference"
                      legend="How much walking feels right?"
                      options={levels}
                      value={profile.walking_preference}
                      onChange={(value) => update("walking_preference", value)}
                      error={errors.walking_preference}
                    />
                    <PreferenceChoices
                      name="crowd_preference"
                      legend="How do you feel about crowds?"
                      options={crowds}
                      value={profile.crowd_preference}
                      onChange={(value) => update("crowd_preference", value)}
                      error={errors.crowd_preference}
                    />
                    <PreferenceChoices
                      name="trip_pace"
                      legend="What pace do you prefer?"
                      options={paces}
                      value={profile.trip_pace}
                      onChange={(value) => update("trip_pace", value)}
                      error={errors.trip_pace}
                    />
                  </>
                )}
                {step === 3 && (
                  <>
                    <PreferenceChoices
                      name="weather_preference"
                      legend="What weather feels comfortable?"
                      options={weatherOptions}
                      value={profile.weather_preference}
                      onChange={(value) => update("weather_preference", value)}
                      error={errors.weather_preference}
                    />
                    <fieldset className="preference-group">
                      <legend>Weather you prefer to avoid</legend>
                      <div className="choice-grid">
                        {weatherDislikes.map(([value, label]) => (
                          <label className="choice" key={value}>
                            <input
                              type="checkbox"
                              checked={profile.weather_dislikes.includes(value)}
                              onChange={(event) =>
                                update(
                                  "weather_dislikes",
                                  event.target.checked
                                    ? [...profile.weather_dislikes, value]
                                    : profile.weather_dislikes.filter(
                                        (item) => item !== value,
                                      ),
                                )
                              }
                            />
                            <span>{label}</span>
                          </label>
                        ))}
                      </div>
                      {fieldError("weather_dislikes")}
                    </fieldset>
                    <fieldset className="preference-group">
                      <legend>Comfort and accessibility</legend>
                      <p className="field-hint">
                        Tell us about travel needs, rather than diagnoses or
                        medical history.
                      </p>
                      <div className="comfort-list">
                        {comforts.map(([value, label]) => (
                          <label className="check-option" key={value}>
                            <input
                              type="checkbox"
                              checked={profile.accessibility.requirements.includes(
                                value,
                              )}
                              onChange={(event) =>
                                update("accessibility", {
                                  ...profile.accessibility,
                                  requirements: event.target.checked
                                    ? [
                                        ...profile.accessibility.requirements,
                                        value,
                                      ]
                                    : profile.accessibility.requirements.filter(
                                        (item) => item !== value,
                                      ),
                                })
                              }
                            />
                            <span>{label}</span>
                          </label>
                        ))}
                      </div>
                    </fieldset>
                    <div className="input-field">
                      <label htmlFor="accessibility-note">
                        Any other practical requirements?{" "}
                        <span>(optional)</span>
                      </label>
                      <input
                        id="accessibility-note"
                        maxLength={500}
                        value={
                          profile.accessibility.additional_requirements ?? ""
                        }
                        onChange={(event) =>
                          update("accessibility", {
                            ...profile.accessibility,
                            additional_requirements: event.target.value || null,
                          })
                        }
                        {...errorProps("accessibility.additional_requirements")}
                      />
                      <p className="field-hint">
                        A single line, up to 500 characters. Please leave out
                        medical details.
                      </p>
                      {fieldError("accessibility.additional_requirements")}
                    </div>
                    <p className="privacy-note">
                      We&apos;ll send these preferences to Roamline for validation.
                      They will not be saved, and no recommendations will be
                      generated yet.
                    </p>
                  </>
                )}
              </fieldset>
              <div className="form-actions">
                {step > 0 ? (
                  <button
                    className="secondary-button"
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setErrors({});
                      moveTo(step - 1);
                    }}
                  >
                    Back
                  </button>
                ) : (
                  <span className="field-hint">
                    A few minutes, at your own pace.
                  </span>
                )}
                <button
                  className="primary-button"
                  type="submit"
                  disabled={busy}
                >
                  {busy
                    ? "Checking preferences..."
                    : step === 3
                      ? "Validate my preferences"
                      : "Continue"}
                </button>
              </div>
              <p role="status" className="submission-status">
                {busy
                  ? "Checking your preferences with Roamline. Please wait."
                  : ""}
              </p>
            </form>
          </div>
        </div>
      )}
    </main>
  );
}
