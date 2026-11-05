"""Travel preferences only; no persistence or recommendation behavior yet."""
from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


class PreferenceLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class WalkingPreference(StrEnum):
    MINIMAL = "minimal"
    BALANCED = "balanced"
    EXTENSIVE = "extensive"


class CrowdPreference(StrEnum):
    QUIET = "quiet"
    BALANCED = "balanced"
    LIVELY = "lively"


class TravelerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    interests: list[str] = Field(default_factory=list, max_length=30)
    hobbies: list[str] = Field(default_factory=list, max_length=30)
    activity_preferences: list[str] = Field(default_factory=list, max_length=30)
    travel_style: str | None = Field(default=None, min_length=1, max_length=100)
    activity_level: PreferenceLevel | None = None
    walking_preference: WalkingPreference | None = None
    heat_tolerance: PreferenceLevel | None = None
    cold_tolerance: PreferenceLevel | None = None
    crowd_preference: CrowdPreference | None = None
    accessibility_requirements: list[str] = Field(default_factory=list, max_length=20)
    comfort_preferences: list[str] = Field(default_factory=list, max_length=20)
