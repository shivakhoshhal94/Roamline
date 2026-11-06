from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.domain.recommendations import RecommendationRequest, RecommendationResponse
from app.services.ranking import recommend

router = APIRouter(prefix="/api/v1/recommendations", tags=["recommendations"])


@router.post("/destinations", response_model=RecommendationResponse)
def destination_recommendations(body: RecommendationRequest, request: Request) -> RecommendationResponse:
    try:
        with Session(request.app.state.engine) as session:
            return recommend(session, body.profile, body.limit, request.app.state.settings)
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail={"code": "destination_storage_unavailable", "message": "Destination data is temporarily unavailable. Please try again later."}) from None
