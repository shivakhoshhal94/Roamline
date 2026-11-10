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
    from app.retrieval.embedding import download_models, MODEL_REVISIONS, SMALL_RERANKER
    local=tmp_path/"snapshot";local.mkdir();(local/"config.json").write_text("{}")
    with patch("huggingface_hub.snapshot_download",return_value=str(local)) as download:
        registry=download_models(tmp_path/"models")
    assert set(registry)==set(MODEL_REVISIONS)-{SMALL_RERANKER}
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


def test_model_specific_formatting_without_loading_weights():
    from app.retrieval.embedding import LocalEncoder, MODELS
    for name in MODELS:
        encoder=object.__new__(LocalEncoder);encoder.name=name
        calls=[]
        class Model:
            def encode(self,texts,**kwargs):
                calls.append((texts,kwargs));return np.ones((len(texts),384),dtype=np.float32)
        encoder.model=Model()
        encoder.encode(['architecture'],query=True);encoder.encode(['museum'])
        if name.startswith('intfloat'):
            assert calls[0][0]==['query: architecture'] and calls[1][0]==['passage: museum']
        else:
            assert calls[0][0]==['Represent this sentence for searching relevant passages: architecture']
            assert calls[1][0]==['museum']
        assert all(kwargs['normalize_embeddings'] for _,kwargs in calls)


def test_embedding_interruption_resume_hash_versions_and_force(retrieval_postgres,city_factory):
    from app.retrieval.embedding import embed_chunks,embedding_status
    db=retrieval_postgres
    with db.factory.begin() as session:
        for identifier in range(1,4):
            city=city_factory(identifier);session.add(city)
            ingest_page(session,city,city.name,identifier,1,datetime.now(timezone.utc),'== See ==\n'+TEXT,'a'*64,0)
    class Interrupted(FakeEncoder):
        calls=0
        def encode(self,texts,query=False):
            self.calls+=1
            if self.calls==2:raise RuntimeError('controlled test interruption')
            return super().encode(texts,query)
    encoder=Interrupted()
    with pytest.raises(RuntimeError,match='controlled test'):
        embed_chunks(db.factory,encoder,batch_size=1)
    assert embedding_status(db.factory,encoder.name,encoder.revision)['already_embedded']==1
    resumed=embed_chunks(db.factory,FakeEncoder(),batch_size=1)
    assert resumed['newly_embedded']==2 and resumed['skipped_unchanged']==1
    assert resumed['final_status']['remaining']==0
    assert embed_chunks(db.factory,FakeEncoder())['newly_embedded']==0
    with db.factory.begin() as session:
        chunk=session.scalar(select(KnowledgeChunk).order_by(KnowledgeChunk.id));chunk.content_hash='c'*64
    assert embedding_status(db.factory,encoder.name,encoder.revision)['remaining']==1
    assert embed_chunks(db.factory,FakeEncoder())['newly_embedded']==1
    newer=FakeEncoder();newer.revision='b'*40
    assert embed_chunks(db.factory,newer)['newly_embedded']==3
    assert embed_chunks(db.factory,newer,force=True)['newly_embedded']==3
    assert embedding_status(db.factory,encoder.name,encoder.revision)['already_embedded']==3


def test_union_deduplicates_geography_and_reranker_is_bounded(retrieval_postgres,city_factory):
    from app.retrieval.embedding import embed_chunks
    db=retrieval_postgres
    with db.factory.begin() as session:
        for identifier,country,continent in [(1,'IT','EU'),(2,'JP','AS'),(3,'BR','SA')]:
            city=city_factory(identifier,country_code=country,continent_code=continent);session.add(city)
            ingest_page(session,city,city.name,identifier,1,datetime.now(timezone.utc),'== See ==\n'+TEXT,'a'*64,0)
    encoder=FakeEncoder();embed_chunks(db.factory,encoder)
    class Bounded:
        def score(self,query,documents):
            assert len(documents)==2
            return [0,1]
    with db.factory() as session:
        result=retrieve(session,RetrievalRequest(query='Roman architecture',top_k=3,candidates=3),encoder,Bounded(),mode='union',rerank_limit=2)
        assert len({v['geoname_id'] for v in result['results']})==3
        assert {v['continent'] for v in result['results']}=={'EU','AS','SA'}
        assert sum(v['reranker_score'] is not None for v in result['results'])==2
        assert result['union_destination_count']==3
        assert result['timings']['total']>=result['timings']['reranker']
        for aggregation in ['best','top2mean','top3mean']:
            actual=retrieve(session,RetrievalRequest(query='Roman architecture',top_k=3,candidates=3,rerank=False),encoder,mode='high_recall_union',aggregation=aggregation)
            assert len(actual['results'])==3
            assert actual['chunk_hits']['dense']>0


def test_top_two_aggregation_is_a_real_ablation(city_factory):
    city=city_factory();page=SimpleNamespace(source='wikivoyage',title='City',source_page_id=1,revision_id=1,source_url='https://example.org',license='CC BY-SA 4.0',ingested_at=datetime.now(timezone.utc))
    rows=[(SimpleNamespace(id=i,section='See',text=TEXT,content_hash='a'*64),page,city,value) for i,value in enumerate([1,.8,.1])]
    assert destination_ranks(rows,'top2mean')['1']['score']==pytest.approx(.9)
    assert destination_ranks(rows,'top3mean')['1']['score']==pytest.approx(1.9/3)


def test_evaluation_split_requires_human_held_out_judgments():
    from app.retrieval.full_evaluate import split_queries,selection_key
    legacy={'id':'old','split':'development','judgment_status':'legacy','relevance':[{'geoname_id':1,'grade':3}]}
    proposed={'id':'new','split':'held_out','judgment_status':'awaiting_human_review','relevance':[]}
    dev,held,pending=split_queries({'queries':[legacy,proposed]})
    assert dev==[legacy] and held==[] and pending==[proposed]
    proposed=dict(proposed,judgment_status='human_reviewed',relevance=[{'geoname_id':2,'grade':2}])
    assert split_queries({'queries':[legacy,proposed]})[1]==[proposed]
    with pytest.raises(ValueError,match='human_reviewed'):
        split_queries({'queries':[dict(proposed,judgment_status='assistant_proposed')]})
    with pytest.raises(ValueError,match='Development judgments'):
        split_queries({'queries':[dict(proposed,split='development',judgment_status='assistant_proposed')]})
    value={'metrics':{'recall@100':.5,'recall@20':.2,'ndcg@10':.3},'timings':{'total':{'p95':1}}}
    assert selection_key(value)==selection_key(value)


def test_full_benchmark_split_is_frozen_and_new_grades_are_empty():
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]
    document=json.loads((root/'evaluation/retrieval/full-corpus-v1/queries-review.json').read_text(encoding='utf-8'))
    split=json.loads((root/'evaluation/retrieval/full-corpus-v1/splits.json').read_text(encoding='utf-8'))
    assert 40<=len(document['queries'])<=60
    assert set(split['development']).isdisjoint(split['held_out'])
    assert set(split['development'])|set(split['held_out'])=={q['id'] for q in document['queries']}
    assert all(not q['relevance'] for q in document['queries'] if q['judgment_status']=='awaiting_human_review')


def test_full_evaluation_refuses_partial_indexes_before_loading_models(retrieval_postgres,city_factory,tmp_path):
    from app.retrieval.full_evaluate import benchmark
    from app.retrieval.embedding import MODELS
    db=retrieval_postgres
    with db.factory.begin() as session:
        city=city_factory();session.add(city)
        ingest_page(session,city,city.name,1,1,datetime.now(timezone.utc),'== See ==\n'+TEXT,'a'*64,0)
    source=tmp_path/'qrels.json';source.write_text(json.dumps({'queries':[{'id':'q','query':'Roman architecture','split':'development','judgment_status':'legacy','relevance':[{'geoname_id':1,'grade':3}]}]}))
    registry={name:{'revision':'a'*40,'path':'unused'} for name in MODELS}
    with patch('app.retrieval.full_evaluate.LocalEncoder',side_effect=AssertionError('No weights loaded')):
        with pytest.raises(ValueError,match='embedding incomplete'):
            benchmark(db.factory,registry,source,tmp_path/'output')


def test_native_hnsw_experiment_is_removed_after_comparison(retrieval_postgres,city_factory):
    from app.retrieval.full_ann import compare_ann
    from app.retrieval.embedding import embed_chunks
    db=retrieval_postgres
    with db.factory.begin() as session:
        city=city_factory();session.add(city)
        ingest_page(session,city,city.name,1,1,datetime.now(timezone.utc),'== See ==\n'+TEXT,'a'*64,0)
    encoder=FakeEncoder();embed_chunks(db.factory,encoder)
    result=compare_ann(db.factory,[{'id':'q','query':'Roman architecture','relevance':[{'geoname_id':1,'grade':3}]}],encoder,{'mode':'hybrid'})
    assert result['pipeline_hnsw']['metrics']['recall@100']==1
    assert result['experimental_index_removed'] and result['hnsw_index_used']
    assert result['index_bytes']>0
    assert result['recall_against_exact']['chunks']['20']==1
    with db.factory() as session:
        assert not session.scalar(text("SELECT count(*) FROM pg_indexes WHERE schemaname=:schema AND indexname='ix_knowledge_full_ann_experiment'"),{'schema':db.engine.get_execution_options()['schema_translate_map'][None]})


def test_review_import_preserves_split_and_requires_reviewer(tmp_path):
    from app.retrieval.full_evaluate import import_human_review
    import csv
    dataset=tmp_path/'queries.json';worksheet=tmp_path/'review.csv'
    dataset.write_text(json.dumps({'queries':[{'id':'q','query':'Historic streets','split':'held_out','judgment_status':'awaiting_human_review','relevance':[]}]}))
    fields=['query_id','query','split','geoname_id','city','grade_0_to_3','evidence_revision_url','reviewer','notes']
    values=['q','Historic streets','held_out',1,'City',3,'https://example.org','','Human test fixture']
    with worksheet.open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(fields);writer.writerow(values)
    with pytest.raises(ValueError,match='reviewer'):import_human_review(worksheet,dataset)
    values[7]='test reviewer'
    with worksheet.open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(fields);writer.writerow(values)
    assert import_human_review(worksheet,dataset)==1
    actual=json.loads(dataset.read_text())['queries'][0]
    assert actual['split']=='held_out' and actual['judgment_status']=='human_reviewed'


def test_full_system_runs_are_deterministic_with_fake_encoder(retrieval_postgres,city_factory):
    from app.retrieval.full_evaluate import run_system
    from app.retrieval.embedding import embed_chunks
    db=retrieval_postgres
    with db.factory.begin() as session:
        for identifier in [2,1]:
            city=city_factory(identifier);session.add(city)
            body=TEXT if identifier==1 else 'Temples and traditional gardens provide cultural experiences. '*10
            ingest_page(session,city,city.name,identifier,1,datetime.now(timezone.utc),'== See ==\n'+body,'a'*64,0)
    encoder=FakeEncoder();embed_chunks(db.factory,encoder)
    queries=[{'id':'roman','query':'Roman architecture','relevance':[{'geoname_id':1,'grade':3}]}]
    first=run_system(db.factory,queries,encoder,{'mode':'union','dense_k':100,'lexical_k':50})
    second=run_system(db.factory,queries,encoder,{'mode':'union','dense_k':100,'lexical_k':50})
    assert first['runs']==second['runs']
    assert first['metrics']['recall@100']==1 and first['metrics']['ndcg@10']==1
    assert first['per_query']['roman']['destination_count']==2


def test_encoding_race_never_persists_a_vector_for_changed_content(retrieval_postgres,city_factory):
    from app.retrieval.embedding import embed_chunks
    db=retrieval_postgres
    with db.factory.begin() as session:
        city=city_factory();session.add(city)
        ingest_page(session,city,city.name,1,1,datetime.now(timezone.utc),'== See ==\n'+TEXT,'a'*64,0)
    class Changing(FakeEncoder):
        def encode(self,texts,query=False):
            with db.factory.begin() as session:
                chunk=session.scalar(select(KnowledgeChunk));chunk.content_hash='f'*64
            return super().encode(texts,query)
    result=embed_chunks(db.factory,Changing())
    assert result['newly_embedded']==0 and result['final_status']['remaining']==1
    with db.factory() as session:
        assert session.scalar(select(func.count()).select_from(KnowledgeEmbedding))==0
    assert embed_chunks(db.factory,FakeEncoder())['newly_embedded']==1


def test_full_benchmark_checkpoints_without_network_or_model_weights(retrieval_postgres,city_factory,tmp_path):
    from app.retrieval import full_evaluate as evaluation
    from app.retrieval.embedding import embed_chunks,MODELS,RERANKER,SMALL_RERANKER
    db=retrieval_postgres
    with db.factory.begin() as session:
        for identifier in [1,2]:
            city=city_factory(identifier);session.add(city)
            ingest_page(session,city,city.name,identifier,1,datetime.now(timezone.utc),'== See ==\n'+TEXT,'a'*64,0)
    class LocalFake(FakeEncoder):
        def __init__(self,name,revision,path):self.name=name;self.revision=revision
    registry={name:{'revision':'a'*40,'path':'unused','bytes':123} for name in [*MODELS,RERANKER,SMALL_RERANKER]}
    for name in MODELS:embed_chunks(db.factory,LocalFake(name,'a'*40,'unused'))
    source=tmp_path/'qrels.json';source.write_text(json.dumps({'queries':[
        {'id':'dev','query':'Roman architecture','split':'development','judgment_status':'legacy','relevance':[{'geoname_id':1,'city':'City 1','grade':3}]},
        {'id':'held','query':'Quiet coastal walks','split':'held_out','judgment_status':'awaiting_human_review','relevance':[]}]}))
    output=tmp_path/'results'
    with patch.object(evaluation,'LocalEncoder',LocalFake),patch.object(evaluation,'LocalReranker',side_effect=lambda *args:FakeReranker()),patch('app.retrieval.full_ann.compare_ann',return_value={'test_stub':True}):
        first=evaluation.benchmark(db.factory,registry,source,output)
        assert len(first['systems'])>=20
        assert first['held_out']['status']=='pending_human_judgments'
        assert first['selection']['held_out_accessed_for_selection'] is False
        assert (output/'latency.json').exists() and (output/'summary.md').exists()
        with patch.object(evaluation,'run_system',side_effect=AssertionError('Cached experiments must not repeat')):
            resumed=evaluation.benchmark(db.factory,registry,source,output)
        assert first['selection']==resumed['selection']
        source.write_text(source.read_text().replace('Roman architecture','Roman history'))
        with pytest.raises(ValueError,match='Dataset/config changed'):
            evaluation.benchmark(db.factory,registry,source,output)


def test_empty_retrieval_is_scored_as_a_miss_without_synthetic_documents():
    from app.retrieval.full_evaluate import metrics
    queries=[{'id':key,'relevance':[{'geoname_id':1,'grade':3}]} for key in ['a','b']]
    empty=metrics(queries,{'a':{},'b':{}})
    assert all(value==0 for value in empty.values())
    partial=metrics(queries,{'a':{'1':1.0},'b':{}})
    assert partial['recall@20']==.5 and partial['mrr']==.5

def test_hash_mismatched_embeddings_are_not_retrieved(retrieval_postgres, city_factory):
    from app.retrieval.embedding import embed_chunks
    db = retrieval_postgres
    with db.factory.begin() as session:
        city = city_factory()
        session.add(city)
        ingest_page(session, city, city.name, 1, 1, datetime.now(timezone.utc), '== See ==\n' + TEXT, 'a' * 64, 0)
    encoder = FakeEncoder()
    embed_chunks(db.factory, encoder)
    request = RetrievalRequest(query='Roman architecture', rerank=False)
    with db.factory() as session:
        assert chunk_ranks(session, request, encoder)
    with db.factory.begin() as session:
        session.scalar(select(KnowledgeChunk)).content_hash = 'f' * 64
    with db.factory() as session:
        assert chunk_ranks(session, request, encoder) == []
    assert embed_chunks(db.factory, encoder)['newly_embedded'] == 1
    with db.factory() as session:
        assert chunk_ranks(session, request, encoder)


def test_embedding_cli_status_and_resume_use_real_storage(retrieval_postgres, city_factory, monkeypatch, capsys):
    from app.retrieval import cli
    from app.core.config import Settings
    db = retrieval_postgres
    with db.factory.begin() as session:
        city = city_factory()
        session.add(city)
        ingest_page(session, city, city.name, 1, 1, datetime.now(timezone.utc), '== See ==\n' + TEXT, 'a' * 64, 0)
    config = Settings(_env_file=None, database_url='postgresql+psycopg://test:test@localhost/test')
    def encoder(name, revision, path, **options):
        assert (name, revision) == (FakeEncoder.name, FakeEncoder.revision)
        assert options == {'threads': 1, 'inference_batch_size': 1}
        return FakeEncoder()
    with patch.object(cli, 'Settings', return_value=config), patch.object(cli, 'create_db_engine', return_value=db.engine), patch.object(db.engine, 'dispose'):
        arguments = ['retrieval', 'embed', '--model', FakeEncoder.name, '--revision', FakeEncoder.revision,
                     '--model-path', 'unused-fixture', '--batch-size', '1', '--threads', '1', '--inference-batch-size', '1']
        with patch('app.retrieval.embedding.LocalEncoder', side_effect=encoder):
            monkeypatch.setattr('sys.argv', arguments)
            cli.main()
            first = capsys.readouterr()
            assert json.loads(first.out)['newly_embedded'] == 1
            assert first.err
            cli.main()
            resumed = json.loads(capsys.readouterr().out)
            assert resumed['newly_embedded'] == 0 and resumed['skipped_unchanged'] == 1
        with patch('app.retrieval.embedding.LocalEncoder', side_effect=AssertionError('Status must not load weights')):
            monkeypatch.setattr('sys.argv', ['retrieval', 'embedding-status', '--model', FakeEncoder.name, '--revision', FakeEncoder.revision])
            cli.main()
            status = json.loads(capsys.readouterr().out)
            assert status['total_chunks'] == status['already_embedded'] == 1
            assert status['remaining'] == 0
