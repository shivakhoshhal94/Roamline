"""Canonical, validation-only travel preferences shared by both journeys."""
from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, StringConstraints,
    field_validator, model_validator,
)


def no_control_characters(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Use plain text without control characters")
    return value


ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
    AfterValidator(no_control_characters),
]
AccessibilityText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    AfterValidator(no_control_characters),
]


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PreferenceLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class TravelStyle(StrEnum):
    RELAXED = "relaxed"
    BALANCED = "balanced"
    ACTIVE = "active"


class WeatherPreference(StrEnum):
    COOL = "cool"
    MILD = "mild"
    WARM = "warm"
    FLEXIBLE = "flexible"


class WeatherDislike(StrEnum):
    STRONG_HEAT = "strong_heat"
    COLD = "cold"
    FREQUENT_RAIN = "frequent_rain"


class CrowdPreference(StrEnum):
    QUIETER = "quieter"
    NEUTRAL = "neutral"
    BUSY_IS_FINE = "busy_is_fine"


class TripPace(StrEnum):
    FEWER_PLACES = "fewer_places"
    BALANCED = "balanced"
    SEE_MORE = "see_more"


class ComfortRequirement(StrEnum):
    AVOID_MANY_STAIRS = "avoid_many_stairs"
    AVOID_STEEP_WALKING = "avoid_steep_walking"
    FREQUENT_REST = "frequent_rest"
    WHEELCHAIR_ACCESS = "wheelchair_access"


class TravelContext(ProfileModel):
    origin_city: ShortText | None = None
    origin_country: ShortText | None = None
    start_date: date | None = None
    end_date: date | None = None
    duration_days: Annotated[int, Field(strict=True, ge=1, le=365)] | None = None
    flexible_dates: bool = False

    @field_validator("origin_city", "origin_country", mode="before")
    @classmethod
    def empty_origin_is_unknown(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def validate_dates_and_duration(self) -> Self:
        if self.start_date is not None and self.end_date is not None:
            days = (self.end_date - self.start_date).days + 1
            if days < 1:
                raise ValueError("End date must be on or after start date")
            if days > 365:
                raise ValueError("Trips must be between 1 and 365 days")
            if self.duration_days is not None and self.duration_days != days:
                raise ValueError("Duration must match the dates, counting both arrival and departure days")
            self.duration_days = days
        elif self.duration_days is None:
            raise ValueError("Provide a duration or both travel dates")
        return self


class Interest(ProfileModel):
    name: ShortText
    importance: PreferenceLevel = PreferenceLevel.MODERATE

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        if isinstance(value, str):
            # Validate control characters before whitespace normalization.
            no_control_characters(value)
            return " ".join(value.split()).casefold()
        return value


class Accessibility(ProfileModel):
    requirements: list[ComfortRequirement] = Field(default_factory=list, max_length=4)
    additional_requirements: AccessibilityText | None = None

    @field_validator("additional_requirements", mode="before")
    @classmethod
    def empty_note_is_unknown(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("requirements")
    @classmethod
    def unique_requirements(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Comfort requirements must be unique")
        return values


class TravelerProfile(ProfileModel):
    travel_context: TravelContext
    # Interests and hobbies share one extensible weighted vocabulary.
    interests: list[Interest] = Field(default_factory=list, max_length=30)
    travel_style: TravelStyle | None = None
    activity_level: PreferenceLevel | None = None
    walking_preference: PreferenceLevel | None = None
    weather_preference: WeatherPreference | None = None
    weather_dislikes: list[WeatherDislike] = Field(default_factory=list, max_length=3)
    crowd_preference: CrowdPreference | None = None
    trip_pace: TripPace | None = None
    accessibility: Accessibility = Field(default_factory=Accessibility)

    @field_validator("interests")
    @classmethod
    def unique_interests(cls, values):
        names = [interest.name for interest in values]
        if len(set(names)) != len(names):
            raise ValueError("Choose each interest only once")
        return values

    @field_validator("weather_dislikes")
    @classmethod
    def unique_weather_dislikes(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Weather dislikes must be unique")
        return values
