from fastapi import APIRouter

from app.domain.profile import TravelerProfile

router = APIRouter(prefix="/api/v1/traveler-profile", tags=["traveler-profile"])


@router.post("/validate", response_model=TravelerProfile)
def validate_profile(profile: TravelerProfile) -> TravelerProfile:
    """Return canonical preferences without storing them or generating travel results."""
    return profile
