"""Deterministic product retrieval. No provider enrichment or generative model calls."""
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.domain.profile import TravelerProfile
from app.retrieval.service import RetrievalRequest, chunk_ranks, retrieve

MODEL = 'BAAI/bge-small-en-v1.5'
REVISION = '5c38ec7c405ec4b44b94cc5a9bb96e735b38267a'
LIMITATIONS = [
    'Retrieval ranks are evidence ordering, not personal-match percentages or probabilities.',
    'Human relevance review and full-corpus quality evaluation remain incomplete.',
    'Dates, duration, weather, crowds, accessibility and travel feasibility are not verified or scored.',
    'Guide text may be outdated. Check original sources for opening hours and availability.',
    'No LLM, reranker, itinerary generation or automatic Geoapify enrichment is used.',
]
CATEGORIES = {
    'history_culture': ('History and culture', r'histor|cultur|heritage|temple|religio'),
    'architecture': ('Architecture', r'architect|building|castle|palace|bridge|tower'),
    'museums': ('Museums', r'museum|galler'),
    'nature': ('Nature', r'nature|park|hiking|forest|beach|mountain|wildlife'),
    'food': ('Food', r'\beat\b|food|cuisine|restaurant|market|drink'),
    'entertainment': ('Entertainment', r'entertain|nightlife|music|theatre|theater|festival|sport'),
    'photography': ('Photography', r'photo|viewpoint|panorama|scenic'),
    'local_experiences': ('Local experiences', r'local|\bdo\b|\bbuy\b|shopping'),
}

class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    profile: TravelerProfile
    countries: list[str] = Field(default_factory=list, max_length=30)
    continents: list[str] = Field(default_factory=list, max_length=7)
    limit: int = Field(default=8, strict=True, ge=1, le=20)

    # Reuse existing canonical geographic validation without accepting search controls.
    @field_validator('countries', 'continents')
    @classmethod
    def codes(cls, values, info):
        return getattr(RetrievalRequest(**{info.field_name: values}, query='travel'), info.field_name)

class DestinationDiscoveryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    destination_id: int = Field(strict=True, gt=0)
    profile: TravelerProfile
    limit: int = Field(default=24, strict=True, ge=1, le=40)


def query_for(profile):
    # Explicit deterministic boundary for future optional interpretation. Never send
    # accessibility free text or origin details into retrieval or model prompts.
    importance = {'high': 0, 'moderate': 1, 'low': 2}
    interests = sorted(profile.interests, key=lambda i: (importance[i.importance], i.name))
    terms = [interest.name for interest in interests]
    style = profile.travel_style.value if hasattr(profile.travel_style, 'value') else profile.travel_style
    return ('Travel guide: ' + ', '.join(terms or ['sights', 'local culture', 'food']) +
            (f'. {style} travel.' if style else ''))[:1500]


def identity(city):
    return dict(geoname_id=city.geoname_id, city=city.name, country=city.country_name,
                country_code=city.country_code, continent=city.continent_code)


def passage(chunk, page):
    return dict(chunk_id=chunk.id, section=chunk.section, snippet=chunk.text[:600],
                source=page.source, title=page.title, revision_id=page.revision_id,
                url=page.source_url, license=page.license, content_hash=chunk.content_hash,
                ingested_at=page.ingested_at.isoformat())


def context(profile, encoder):
    return dict(query=query_for(profile), duration_days=profile.travel_context.duration_days,
                limitations=LIMITATIONS, embedding_model={'name': encoder.name, 'revision': encoder.revision},
                method='BGE dense + PostgreSQL lexical + reciprocal rank fusion',
                preference_handling='Interest names and travel style form a deterministic query. Importance orders terms; it is not a calibrated weight.')


def discover(session, body, encoder):
    request = RetrievalRequest(query=query_for(body.profile), countries=body.countries,
        continents=body.continents, top_k=body.limit, candidates=50, rerank=False)
    output = retrieve(session, request, encoder=encoder)
    for item in output['results']:
        channels = ['semantic retrieval'] if item['dense_rank'] else []
        if item['lexical_rank']: channels.append('text search')
        item['reasons'] = ['Guide passages surfaced through ' + ' and '.join(channels) +
            ' for your interest query.', 'Supporting guide sections: ' + ', '.join(dict.fromkeys(e['section'] for e in item['evidence'])) + '.']
    return {**context(body.profile, encoder), 'scope': output['scope'], 'results': output['results']}


def destination_discover(session, body, encoder):
    request = RetrievalRequest(query=query_for(body.profile), destination_id=body.destination_id,
                               rerank=False)
    vector = encoder.encode([request.query], query=True)[0].tolist()
    dense = chunk_ranks(session, request, encoder, vector, limit=100)
    lexical = chunk_ranks(session, request, mode='lexical', limit=100)
    rows, scores = {}, {}
    for channel in (dense, lexical):
        for rank, row in enumerate(channel, 1):
            key = row[0].id
            rows[key] = row
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    groups = {key: {'key': key, 'title': title, 'evidence': []} for key, (title, _) in CATEGORIES.items()}
    for key in sorted(scores, key=lambda key: (-scores[key], key))[:body.limit]:
        chunk, page, _, _ = rows[key]
        # Section classification is organizational, not a relevance judgment.
        section = chunk.section.casefold()
        group = next((key for key, (_, pattern) in CATEGORIES.items() if re.search(pattern, section)), None)
        if group is None:
            text = chunk.text.casefold()
            group = next((key for key, (_, pattern) in CATEGORIES.items() if re.search(pattern, text)), 'local_experiences')
        groups[group]['evidence'].append({**passage(chunk, page), 'retrieval_rank': sum(len(g['evidence']) for g in groups.values()) + 1})
    return {**context(body.profile, encoder), 'categories': list(groups.values())}

class DestinationIdentity(BaseModel):
    geoname_id: int
    city: str
    country: str
    country_code: str
    continent: str

class GuideEvidence(BaseModel):
    chunk_id: int
    section: str
    snippet: str
    source: str
    title: str
    revision_id: int
    url: str
    license: str
    content_hash: str
    ingested_at: str

class DestinationMatch(DestinationIdentity):
    rank: int
    reasons: list[str]
    evidence: list[GuideEvidence]
    dense_rank: int | None
    lexical_rank: int | None
    retrieval_score: float

class DiscoveryContext(BaseModel):
    query: str
    duration_days: int
    limitations: list[str]
    embedding_model: dict[str, str]
    method: str
    preference_handling: str

class GlobalDiscoveryResponse(DiscoveryContext):
    scope: str
    results: list[DestinationMatch]

class GuideCategory(BaseModel):
    key: str
    title: str
    evidence: list[GuideEvidence]

class DestinationDiscoveryResponse(DiscoveryContext):
    destination: DestinationIdentity
    categories: list[GuideCategory]

class DestinationLookupResponse(BaseModel):
    results: list[DestinationIdentity]
