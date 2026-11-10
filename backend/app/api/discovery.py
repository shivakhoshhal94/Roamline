"""Product discovery API, independent of Geoapify's evidence-based scoring."""
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select, exists, or_, func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.db.models import Destination
from app.knowledge.models import KnowledgePage
from app.retrieval.product import (MODEL, REVISION, DiscoveryRequest, DestinationDiscoveryRequest,
    discover, destination_discover, identity, GlobalDiscoveryResponse, DestinationDiscoveryResponse, DestinationLookupResponse)

router = APIRouter(prefix='/api/v1/discovery', tags=['discovery'])


def encoder_for(app):
    if app.state.retrieval_encoder is None:
        if not app.state.settings.retrieval_model_path:
            raise HTTPException(503, detail={'code': 'retrieval_not_configured', 'message': 'Local retrieval model is not configured.'})
        try:
            from app.retrieval.embedding import LocalEncoder
            app.state.retrieval_encoder = LocalEncoder(MODEL, REVISION,
                app.state.settings.retrieval_model_path, threads=app.state.settings.retrieval_threads)
        except (ImportError, OSError, ValueError):
            raise HTTPException(503, detail={'code': 'retrieval_not_available', 'message': 'Pinned local retrieval model could not be loaded.'}) from None
    return app.state.retrieval_encoder


def known_destination(session, destination_id):
    city = session.get(Destination, destination_id)
    if city is None or not session.scalar(select(exists().where(KnowledgePage.destination_id == destination_id))):
        raise HTTPException(404, detail={'code': 'destination_knowledge_missing', 'message': 'No guide knowledge is indexed for this destination.'})
    return city


@router.get('/destinations', response_model=DestinationLookupResponse)
def destinations(request: Request, q: str = Query(min_length=2, max_length=80), limit: int = Query(default=10, ge=1, le=20)):
    try:
        with Session(request.app.state.engine) as session:
            term = q.strip().casefold()
            if len(term) < 2: raise HTTPException(422, detail='Enter at least two characters.')
            pattern = '%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            rows = session.scalars(select(Destination).where(
                exists().where(KnowledgePage.destination_id == Destination.geoname_id),
                or_(func.lower(Destination.name).like(pattern, escape='\\'), func.lower(Destination.ascii_name).like(pattern, escape='\\'))
            ).order_by(Destination.population.desc(), Destination.geoname_id).limit(limit))
            return {'results': [identity(city) for city in rows]}
    except SQLAlchemyError:
        raise HTTPException(503, detail={'code': 'retrieval_storage_unavailable'}) from None


def run(request, body, focused=False):
    # Serialize local CPU inference and lazy loading. No request starts a worker,
    # downloads weights, or invokes a provider. DB statement work is bounded.
    with request.app.state.retrieval_lock:
        try:
            with Session(request.app.state.engine) as session:
                session.execute(text("SET LOCAL statement_timeout = '30000'"))
                city = known_destination(session, body.destination_id) if focused else None
                encoder = encoder_for(request.app)
                result = destination_discover(session, body, encoder) if focused else discover(session, body, encoder)
                if city is not None: result['destination'] = identity(city)
                return result
        except SQLAlchemyError:
            raise HTTPException(503, detail={'code': 'retrieval_storage_unavailable', 'message': 'Guide search is temporarily unavailable.'}) from None


@router.post('/global', response_model=GlobalDiscoveryResponse)
def global_discovery(body: DiscoveryRequest, request: Request):
    return run(request, body)


@router.post('/destination', response_model=DestinationDiscoveryResponse)
def destination_discovery(body: DestinationDiscoveryRequest, request: Request):
    return run(request, body, focused=True)
