"""HTTP integration against isolated real PostgreSQL + vector, no external calls."""
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app
from app.knowledge.ingest import ingest_page
from app.retrieval.embedding import embed_chunks
from app.retrieval.product import DiscoveryRequest, DestinationDiscoveryRequest, discover, query_for
from test_knowledge_retrieval import retrieval_postgres, FakeEncoder

PROFILE = {'travel_context': {'duration_days': 5}, 'interests': [{'name': 'Roman architecture', 'importance': 'high'}, {'name': 'food', 'importance': 'moderate'}], 'travel_style': 'balanced'}

@pytest.fixture
def guide_client(retrieval_postgres, city_factory):
    db = retrieval_postgres
    with db.factory.begin() as session:
        for ident, country, continent, name in [(1,'IT','EU','Rome'),(2,'JP','AS','Kyoto')]:
            city = city_factory(ident,name=name,ascii_name=name,country_code=country,continent_code=continent)
            session.add(city)
            body = ('Roman architecture, museums and historic buildings. ' if ident == 1 else 'Temples and traditional Japanese gardens. ') * 12
            ingest_page(session,city,name,ident,5,datetime.now(timezone.utc), '== Architecture ==\n'+body+'\n== Eat ==\n'+('Local food restaurants and markets. '*20), 'a'*64,0)
    encoder = FakeEncoder()
    embed_chunks(db.factory,encoder)
    config=Settings(_env_file=None,database_url='postgresql+psycopg://test:test@localhost/test')
    with patch('app.main.create_db_engine',return_value=db.engine), patch.object(db.engine,'dispose'):
        app=create_app(config)
        with TestClient(app) as client:
            app.state.retrieval_encoder=encoder
            with patch('socket.create_connection',side_effect=AssertionError('External network forbidden')):
                yield client,app


def test_global_api_worldwide_filter_and_provenance(guide_client):
    client,_=guide_client
    result=client.post('/api/v1/discovery/global',json={'profile':PROFILE}).json()
    assert result['scope']=='worldwide'
    assert {row['continent'] for row in result['results']}=={'EU','AS'}
    assert result['results'][0]['city']=='Rome'
    assert result['duration_days']==5
    for row in result['results']:
        assert row['evidence'] and row['reasons']
        assert 'score' not in row and 'match_percentage' not in row
        assert 'aggregation_evidence' not in row
        for evidence in row['evidence']:
            assert evidence['revision_id']==5 and evidence['source']=='wikivoyage'
            assert 'oldid=5' in evidence['url']
            assert evidence['license'] and len(evidence['content_hash'])==64
    filtered=client.post('/api/v1/discovery/global',json={'profile':PROFILE,'countries':['jp'],'continents':['as'],'limit':1})
    assert filtered.status_code==200
    assert [r['city'] for r in filtered.json()['results']]==['Kyoto']
    assert client.post('/api/v1/discovery/global',json={'profile':PROFILE,'countries':['ZZ']}).json()['results']==[]


def test_destination_scoped_passages_categories_and_no_duplicates(guide_client):
    client,_=guide_client
    response=client.post('/api/v1/discovery/destination',json={'destination_id':1,'profile':PROFILE})
    assert response.status_code==200,response.text
    data=response.json()
    assert data['destination']['city']=='Rome' and len(data['categories'])==8
    evidence=[e for c in data['categories'] for e in c['evidence']]
    assert len(evidence)>=2 and len({e['chunk_id'] for e in evidence})==len(evidence)
    assert all(e['title']=='Rome' for e in evidence)
    assert next(c for c in data['categories'] if c['key']=='architecture')['evidence']
    assert next(c for c in data['categories'] if c['key']=='food')['evidence']
    assert client.post('/api/v1/discovery/destination',json={'destination_id':999,'profile':PROFILE}).status_code==404


def test_lookup_validation_and_missing_model(guide_client):
    client,app=guide_client
    assert client.get('/api/v1/discovery/destinations?q=ro').json()['results'][0]['city']=='Rome'
    assert client.get('/api/v1/discovery/destinations?q=%25%25').json()['results']==[]
    assert client.get('/api/v1/discovery/destinations?q=%20%20').status_code==422
    assert client.post('/api/v1/discovery/global',json={'profile':PROFILE,'continents':['XX']}).status_code==422
    assert client.post('/api/v1/discovery/global',json={'profile':PROFILE,'limit':True}).status_code==422
    assert client.post('/api/v1/discovery/global',json={'profile':PROFILE,'limit':21}).status_code==422
    assert client.post('/api/v1/discovery/global',json={'profile':PROFILE,'rerank':True}).status_code==422
    assert client.post('/api/v1/discovery/destination',json={'destination_id':1,'profile':{}}).status_code==422
    app.state.retrieval_encoder=None
    response=client.post('/api/v1/discovery/global',json={'profile':PROFILE})
    assert response.status_code==503 and response.json()['detail']['code']=='retrieval_not_configured'


def test_query_is_deterministic_and_does_not_include_sensitive_context():
    body=DiscoveryRequest(profile={**PROFILE,'accessibility':{'additional_requirements':'private requirement'}})
    assert query_for(body.profile)=='Travel guide: roman architecture, food. balanced travel.'
    assert 'private' not in query_for(body.profile)
    assert DestinationDiscoveryRequest(destination_id=1,profile=PROFILE).profile.travel_context.duration_days==5


def test_global_policy_fuses_mean_top_two_and_preserves_shortlist_limits(city_factory):
    page = SimpleNamespace(
        source="wikivoyage", title="Guide", source_page_id=1, revision_id=5,
        source_url="https://en.wikivoyage.org/w/index.php?oldid=5",
        license="CC BY-SA 4.0", ingested_at=datetime.now(timezone.utc),
    )
    cities = {identifier: city_factory(identifier) for identifier in range(1, 23)}

    def hit(chunk_id, city_id, score):
        chunk = SimpleNamespace(id=chunk_id, section="See", text="Guide passage", content_hash="a" * 64)
        return chunk, page, cities[city_id], score

    # Best-chunk ranking would put city 1 ahead of city 2.
    dense = [hit(11, 1, .99), hit(21, 2, .9), hit(22, 2, .8), hit(12, 1, .1)]
    dense.extend(hit(100 + identifier, identifier, .05) for identifier in range(4, 23))
    lexical = [hit(31, 3, .95), hit(23, 2, .7), hit(24, 2, .5)]
    encoder = FakeEncoder()
    with patch("app.retrieval.service.chunk_ranks", side_effect=[dense, lexical, dense, lexical]):
        results = discover(None, DiscoveryRequest(profile=PROFILE, limit=20), encoder)["results"]
        default = discover(None, DiscoveryRequest(profile=PROFILE), encoder)["results"]

    assert len(results) == len({row["geoname_id"] for row in results}) == 20
    assert default == results[:8]
    assert [row["geoname_id"] for row in results[:3]] == [2, 3, 1]
    shared, lexical_only, dense_only = results[:3]
    assert (shared["dense_rank"], shared["lexical_rank"], shared["fused_rank"]) == (1, 2, 1)
    assert shared["dense_score"] == pytest.approx(.85)
    assert shared["lexical_score"] == pytest.approx(.6)
    assert shared["retrieval_score"] == pytest.approx(1 / 61 + 1 / 62)
    assert shared["aggregation_evidence"] == {
        "dense": [{"chunk_id": 21, "chunk_score": .9}, {"chunk_id": 22, "chunk_score": .8}],
        "lexical": [{"chunk_id": 23, "chunk_score": .7}, {"chunk_id": 24, "chunk_score": .5}],
    }
    assert lexical_only["dense_rank"] is None and lexical_only["lexical_rank"] == 1
    assert dense_only["dense_rank"] == 2 and dense_only["lexical_rank"] is None
