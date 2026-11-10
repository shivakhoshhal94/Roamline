"""Temporary native pgvector HNSW comparison on the full model-specific corpus."""
from time import perf_counter
from sqlalchemy import Index,text,event
from ranx import Qrels,Run,evaluate
from app.knowledge.models import KnowledgeEmbedding
from app.retrieval.service import chunk_ranks,destination_ranks,RetrievalRequest
from app.retrieval.full_evaluate import distribution,run_system


def compare_ann(factory,queries,encoder,pipeline_options=None,aggregation="best",reranker=None,rerank_limit=None):
    index=Index('ix_knowledge_full_ann_experiment',KnowledgeEmbedding.embedding,
        postgresql_using='hnsw',postgresql_with={'m':16,'ef_construction':64},postgresql_ops={'embedding':'vector_cosine_ops'},
        postgresql_where=(KnowledgeEmbedding.model==encoder.name)&(KnowledgeEmbedding.revision==encoder.revision))
    engine=factory.kw['bind'];before=perf_counter();created=False
    measured={'exact':[],'hnsw':[]};rows=[];plans=[];captured=[]
    def capture(connection,cursor,statement,parameters,context,executemany):
        if statement.lstrip().startswith('SELECT') and 'knowledge_embeddings' in statement:
            captured.append((statement,parameters))
    event.listen(engine,'before_cursor_execute',capture)
    try:
        with engine.begin() as c:
            c.execute(text("SET LOCAL maintenance_work_mem='512MB'"))
            index.create(c);created=True
            c.execute(text('ANALYZE knowledge_embeddings'))
            schema=engine.get_execution_options().get('schema_translate_map',{}).get(None,'public') or 'public'
            size=c.scalar(text('SELECT pg_relation_size(to_regclass(:name))'),{'name':f'{schema}.{index.name}'})
        build=perf_counter()-before
        for query in queries:
            vector=encoder.encode([query['query']],query=True)[0].tolist()
            request=RetrievalRequest(query=query['query'],rerank=False)
            values={}
            for kind in ['exact','hnsw']:
                with factory() as session:
                    if kind=='hnsw':
                        session.execute(text('SET LOCAL enable_seqscan=off'))
                        session.execute(text('SET LOCAL enable_bitmapscan=off'))
                        session.execute(text('SET LOCAL enable_sort=off'))
                        session.execute(text("SET LOCAL plan_cache_mode='force_custom_plan'"))
                        session.execute(text('SET LOCAL hnsw.ef_search=400'))
                        session.execute(text("SET LOCAL hnsw.iterative_scan='strict_order'"))
                        session.execute(text('SET LOCAL hnsw.max_scan_tuples=50000'))
                    before=perf_counter()
                    values[kind]=chunk_ranks(session,request,encoder,vector,exact=kind=='exact',limit=3000)
                    measured[kind].append(perf_counter()-before)
                    if kind=='hnsw' and not plans:
                        statement,parameters=captured[-1]
                        plan=session.connection().exec_driver_sql('EXPLAIN (FORMAT JSON) '+statement,parameters).scalar()[0]['Plan']
                        def walk(node):
                            plans.append({'type':node['Node Type'],'index':node.get('Index Name')})
                            for child in node.get('Plans',[]):walk(child)
                        walk(plan)
            rows.append((query['id'],values))
        pipeline=run_system(factory,queries,encoder,dict(pipeline_options,exact=False),aggregation,reranker,rerank_limit) if pipeline_options is not None else None
        recall={}
        for unit in ['chunks','destinations']:
            recall[unit]={}
            for cutoff in [20,50,100]:
                qrels,runs={},{}
                for identifier,values in rows:
                    def identifiers(items):
                        return [str(row[0].id) for row in items] if unit=='chunks' else list(destination_ranks(items))
                    exact=identifiers(values['exact'])[:cutoff];approx=identifiers(values['hnsw'])[:cutoff]
                    qrels[identifier]={key:1 for key in exact};runs[identifier]={key:float(len(approx)-i) for i,key in enumerate(approx)}
                recall[unit][str(cutoff)]=float(evaluate(Qrels(qrels),Run(runs),f'recall@{cutoff}',make_comparable=True))
        return {'queries':len(queries),'model':encoder.name,'revision':encoder.revision,'build_seconds':build,'index_bytes':size,
            'pipeline_hnsw':pipeline,'database_seconds':{key:distribution(value) for key,value in measured.items()},'recall_against_exact':recall,
            'plan':plans,'hnsw_index_used':any(v['index']==index.name for v in plans),'ef_search':400,
            'iterative_scan':'strict_order','chunk_pool':3000,'m':16,'ef_construction':64,'plan_cache_mode':'force_custom_plan','experimental_index_removed':True}
    finally:
        event.remove(engine,'before_cursor_execute',capture)
        if created:
            with engine.begin() as c:index.drop(c,checkfirst=True)
        KnowledgeEmbedding.__table__.indexes.discard(index)
