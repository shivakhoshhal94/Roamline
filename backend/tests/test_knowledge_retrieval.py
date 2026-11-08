from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import json
import os
from uuid import uuid4
import numpy as np
import pytest
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.orm import Session, sessionmaker
import mwparserfromhell as wiki
import mwxml
from app.db.models import Base, Destination
from app.knowledge.models import KnowledgePage, KnowledgeChunk, KnowledgeEmbedding
from app.knowledge.ingest import section_chunks, clean_section, coordinates, city_lookup, match_city, ingest_page, ingest_dump
from app.retrieval.service import RetrievalRequest, destination_ranks, fuse_destinations, retrieve, chunk_ranks

TEXT = "Historic Roman architecture and museums. Street food markets and cultural sights offer visitors many interesting places to explore. " * 5


def test_wiki_cleaner_preserves_sections_listings_without_navigation():
    code = wiki.parse("{{pagebanner|x.jpg}}{{geo|52.5|13.4}}\n== See ==\n{{see|name=Historic museum|content=" + TEXT + "}} [[File:a.jpg|[[File:nested.jpg]]]]<ref>citation</ref>\n== Eat ==\n" + TEXT)
    chunks = section_chunks("Berlin", code)
    assert {heading for heading, _ in chunks} == {"See", "Eat"}
    combined = " ".join(content for _, content in chunks)
    assert "Historic museum" in combined and "Roman architecture" in combined
    assert "pagebanner" not in combined and "a.jpg" not in combined and "citation" not in combined
    assert coordinates(code) == (52.5, 13.4)


def test_section_split_is_bounded_and_keeps_heading():
    chunks = section_chunks("City", wiki.parse("== Understand ==\n" + TEXT * 30))
    assert len(chunks) > 5
    assert all(heading == "Understand" and len(content) <= 1450 for heading, content in chunks)
    assert section_chunks("Empty", wiki.parse("{{pagebanner|x}}")) == []


def test_identity_is_worldwide_and_rejects_ambiguity_or_missing_geo(city_factory):
    paris = city_factory(1, name="Paris", ascii_name="Paris", latitude=48.85, longitude=2.35, country_code="FR")
    texas = city_factory(2, name="Paris", ascii_name="Paris", latitude=33.66, longitude=-95.55, country_code="US")
    lookup = city_lookup([paris, texas])
    assert match_city("Paris (Texas)", wiki.parse("{{geo|33.66|-95.55}}"), lookup)[1].geoname_id == 2
    assert match_city("Paris/Old town", wiki.parse("{{geo|48.85|2.35}}"), lookup)[1].geoname_id == 1
    assert match_city("Paris", wiki.parse("no coordinates"), lookup) is None
    assert match_city("Paris", wiki.parse("{{geo|NaN|2}}"), lookup) is None
    duplicate = city_factory(3, name="Paris", ascii_name="Paris", latitude=48.851, longitude=2.351)
    assert match_city("Paris", wiki.parse("{{geo|48.85|2.35}}"), city_lookup([paris, duplicate])) is None


def test_ingestion_hash_idempotency_replaces_chunks_and_invalidates_embeddings(database, city_factory):
    with database.factory.begin() as session:
        city = city_factory()
        session.add(city)
        assert ingest_page(session, city, "City 1", 10, 20, datetime.now(timezone.utc), "== See ==\n" + TEXT, "a"*64, 0)[0] == "written"
    with database.factory.begin() as session:
        city = session.get(Destination, 1)
        chunk = session.scalar(select(KnowledgeChunk))
        original_hash = chunk.content_hash
        assert ingest_page(session, city, "City 1", 10, 20, datetime.now(timezone.utc), "== See ==\n" + TEXT, "a"*64, 0)[0] == "unchanged"
        assert ingest_page(session, city, "City 1", 10, 19, datetime.now(timezone.utc), "old", "a"*64, 0)[0] == "older"
        assert ingest_page(session, city, "City 1", 10, 21, datetime.now(timezone.utc), "== Eat ==\n" + TEXT, "b"*64, 0)[0] == "written"
    with database.factory() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgePage)) == 1
        assert session.scalar(select(KnowledgeChunk)).content_hash != original_hash


def test_missing_and_corrupt_dump_fail_without_invented_content(database, tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest_dump(database.factory, tmp_path / "absent.xml")
    broken = tmp_path / "broken.xml"
    broken.write_text("<mediawiki><broken>")
    with pytest.raises(mwxml.errors.MalformedXML):
        ingest_dump(database.factory, broken)
    with database.factory() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgePage)) == 0


def test_latest_revision_and_content_namespace_only(database, city_factory, tmp_path):
    # mwxml is responsible for XML parsing. Small typed page iterators isolate
    # current-revision/namespace policy from library internals.
    with database.factory.begin() as session:
        session.add(city_factory())
    def page(identifier, namespace, revisions):
        class Page:
            id, title, redirect = identifier, "City 1", None
            def __iter__(self): return iter(revisions)
        value = Page(); value.namespace = namespace
        return value
    def revision(identifier, content):
        return SimpleNamespace(id=identifier, text="{{geo|52.5|13.4}}\n== See ==\n" + content, timestamp="2026-01-01T00:00:00Z")
    source = tmp_path / "current.xml"; source.write_text("placeholder")
    with patch("mwxml.Dump.from_file", return_value=[page(1, 0, [revision(1,"Old content "*50), revision(2,TEXT)]), page(2,1,[revision(3,TEXT)])]):
        report = ingest_dump(database.factory, source)
    assert report["pages_processed"] == 1
    with database.factory() as session:
        assert session.scalar(select(KnowledgePage)).revision_id == 2
        assert "Old content" not in session.scalar(select(KnowledgeChunk)).text


def test_best_chunk_aggregation_suppresses_duplicates_and_keeps_provenance(city_factory):
    city = city_factory()
    page = SimpleNamespace(source="wikivoyage",title="City",source_page_id=5,revision_id=6,source_url="https://example.org",license="CC BY-SA 4.0",ingested_at=datetime.now(timezone.utc))
    rows = [(SimpleNamespace(id=index,section="See",text=TEXT,content_hash="a"*64), page, city, 1-index/100) for index in range(10)]
    result = destination_ranks(rows)
    assert len(result) == 1 and len(result["1"]["evidence"]) == 3
    assert result["1"]["score"] == 1
    assert result["1"]["evidence"][0]["revision_id"] == 6
    assert destination_ranks(rows, "top3mean")["1"]["score"] < 1


def test_rrf_uses_standard_library_and_rewards_channel_agreement():
    result = fuse_destinations({"1":{},"2":{}},{"2":{},"3":{}})
    assert result["2"] > result["1"]
    assert fuse_destinations({}, {}) == {}
    assert fuse_destinations({"1":{}}, {})["1"] > 0


@pytest.mark.parametrize("values", [{"query":" "},{"query":"abc","continents":["XX"]},{"query":"abc","countries":["USA"]},{"query":"abc","top_k":0}])
def test_retrieval_contract_rejects_invalid_input(values):
    with pytest.raises(ValueError): RetrievalRequest(**values)


@pytest.fixture
def retrieval_postgres():
    if not os.getenv("TEST_DATABASE_URL"): pytest.skip("PostgreSQL integration URL not set")
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    schema = "retrieval_test_" + uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped = engine.execution_options(schema_translate_map={None:schema})
        Base.metadata.create_all(scoped)
        yield SimpleNamespace(engine=scoped,factory=sessionmaker(scoped,expire_on_commit=False))
    finally:
        with engine.begin() as connection: connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


class FakeEncoder:
    name, revision = "fake-test-model", "a"*40
    def encode(self, texts, query=False):
        vectors = np.zeros((len(texts),384),dtype=np.float32)
        for i, content in enumerate(texts): vectors[i, 0 if "Roman" in content else 1] = 1
        return vectors


class FakeReranker:
    def score(self, query, documents): return [float("Roman" in text) for text in documents]


def test_postgres_vector_lexical_hybrid_filters_and_reranking(retrieval_postgres, city_factory):
    from app.retrieval.embedding import embed_chunks
    database = retrieval_postgres
    with database.factory.begin() as session:
        for identifier, country, continent, name in [(1,"IT","EU","Rome"),(2,"JP","AS","Kyoto"),(3,"BR","SA","Rio")]:
            city = city_factory(identifier,name=name,ascii_name=name,country_code=country,continent_code=continent)
            session.add(city)
            body = TEXT if identifier == 1 else "Temples, beaches and markets offer varied cultural experiences. " * 10
            ingest_page(session, city, name, identifier, 1, datetime.now(timezone.utc), "== See ==\n"+body, "a"*64, 0)
    encoder = FakeEncoder()
    assert embed_chunks(database.factory,encoder)["embedded_chunks"] > 0
    assert embed_chunks(database.factory,encoder)["embedded_chunks"] == 0
    with database.factory() as session:
        assert session.scalar(select(KnowledgeEmbedding)).revision == encoder.revision
        rows = chunk_ranks(session,RetrievalRequest(query="Roman architecture",rerank=False),encoder)
        assert rows[0][2].name == "Rome"
        lexical = retrieve(session,RetrievalRequest(query="Roman architecture",rerank=False),mode="lexical")
        assert lexical["results"][0]["city"] == "Rome"
        worldwide = retrieve(session,RetrievalRequest(query="Roman architecture",top_k=3,candidates=3),encoder,FakeReranker())
        assert {item["continent"] for item in worldwide["results"]} == {"EU","AS","SA"}
        assert worldwide["results"][0]["city"] == "Rome"
        assert all(item["reranker_score"] is not None for item in worldwide["results"])
        asia = retrieve(session,RetrievalRequest(query="Temples",continents=["AS"],top_k=1,candidates=3,rerank=False),encoder)
        assert len(asia["results"]) == 1 and asia["results"][0]["city"] == "Kyoto"
        assert asia["results"][0]["evidence"][0]["source"] == "wikivoyage"
        assert asia["results"][0]["dense_rank"] == 1
        assert retrieve(session,RetrievalRequest(query="Temples",countries=["ZZ"],rerank=False),encoder)["results"] == []
        assert retrieve(session,RetrievalRequest(query="Temples",min_population=1_000_000,rerank=False),encoder)["results"] == []
    with database.factory.begin() as session:
        city = session.get(Destination,1)
        ingest_page(session,city,"Rome",1,2,datetime.now(timezone.utc),"== Eat ==\n"+TEXT,"b"*64,0)
    with database.factory() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeEmbedding).join(KnowledgeChunk).join(KnowledgePage).where(KnowledgePage.destination_id==1)) == 0


def test_cli_lexical_contract_needs_no_model_or_network(database, city_factory, monkeypatch, capsys):
    from app.retrieval import cli
    from app.core.config import Settings
    monkeypatch.setattr("sys.argv", ["retrieval", "search", "Roman architecture", "--mode", "lexical", "--country", "it", "--continent", "eu", "--top-k", "5"])
    def check(session, request, encoder, reranker, mode):
        assert request.countries == ["IT"] and request.continents == ["EU"]
        assert request.top_k == 5 and not request.rerank and mode == "lexical"
        assert encoder is None and reranker is None
        return {"results": [], "scope": "explicit_geographic_filter"}
    with patch.object(cli, "Settings", return_value=Settings(_env_file=None,database_url="postgresql+psycopg://test:test@localhost/test")), patch.object(cli,"create_db_engine",return_value=database.engine), patch.object(database.engine,"dispose"), patch("app.retrieval.service.retrieve",side_effect=check), patch("app.retrieval.embedding.LocalEncoder",side_effect=AssertionError("No transformer loading")):
        cli.main()
    assert json.loads(capsys.readouterr().out)["results"] == []


def test_cli_rejects_dense_search_without_pinned_local_model(monkeypatch):
    from app.retrieval.cli import main
    monkeypatch.setattr("sys.argv", ["retrieval", "search", "historic streets"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_reranker_bad_output_is_rejected(retrieval_postgres, city_factory):
    database = retrieval_postgres
    with database.factory.begin() as session:
        city=city_factory();session.add(city)
        ingest_page(session,city,"City 1",1,1,datetime.now(timezone.utc),"== See ==\n"+TEXT,"a"*64,0)
    class Bad:
        def score(self,query,documents): return [float("nan")]
    with database.factory() as session:
        with pytest.raises(ValueError,match="Invalid reranker"):
            retrieve(session,RetrievalRequest(query="Roman architecture"),reranker=Bad(),mode="lexical")


def test_model_download_is_explicit_pinned_and_mocked(tmp_path):
    from app.retrieval.embedding import download_models, MODEL_REVISIONS
    local=tmp_path/"snapshot";local.mkdir();(local/"config.json").write_text("{}")
    with patch("huggingface_hub.snapshot_download",return_value=str(local)) as download:
        registry=download_models(tmp_path/"models")
    assert set(registry)==set(MODEL_REVISIONS)
    assert download.call_count==3
    assert all(call.kwargs["revision"] in MODEL_REVISIONS.values() and call.kwargs["token"] is False for call in download.call_args_list)
    assert (tmp_path/"models/registry.json").exists()


def test_postgres_frozen_corpus_is_repeatable_and_detects_source_drift(retrieval_postgres, city_factory, tmp_path):
    from app.retrieval.evaluate import freeze_corpus
    database=retrieval_postgres
    with database.factory.begin() as session:
        city=city_factory();city.place_resolution_evidence={"resolution_policy":"existing-product-state"};session.add(city)
        ingest_page(session,city,"City 1",1,1,datetime.now(timezone.utc),"== See ==\n"+TEXT,"a"*64,0)
    manifest=tmp_path/"corpus.json"
    first, document=freeze_corpus(database.factory,manifest)
    second, same=freeze_corpus(database.factory,manifest)
    assert first==second and document==same and len(first)>0
    with database.factory.begin() as session:
        session.get(KnowledgeChunk,first[0]).content_hash="b"*64
    with pytest.raises(ValueError,match="Frozen corpus no longer matches"):
        freeze_corpus(database.factory,manifest)
