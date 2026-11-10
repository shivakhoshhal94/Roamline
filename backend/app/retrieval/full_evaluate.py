"""Full-corpus engineering experiments; human review gates held-out quality claims."""
import argparse
from collections import Counter
from hashlib import sha256
import json
import platform
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
import numpy as np
from ranx import Qrels, Run, evaluate
from scipy.stats import spearmanr
from sqlalchemy import select, func, create_engine, text
from sqlalchemy.orm import sessionmaker
from app.core.config import Settings
from app.db.models import Destination
from app.knowledge.models import KnowledgeChunk, KnowledgePage, KnowledgeEmbedding
from app.retrieval.embedding import MODELS, RERANKER, SMALL_RERANKER, LocalEncoder, LocalReranker, embedding_status
from app.retrieval.service import RetrievalRequest, retrieve

METRICS = ['recall@10','recall@20','recall@50','recall@100','mrr','ndcg@5','ndcg@10','recall@150']
MODES = {
    'dense': {'mode':'dense','dense_k':100},
    'lexical': {'mode':'lexical','lexical_k':100},
    'rrf': {'mode':'hybrid','dense_k':100,'lexical_k':100},
    'union': {'mode':'union','dense_k':50,'lexical_k':50},
    'high_recall_union': {'mode':'high_recall_union','dense_k':100,'lexical_k':50},
}
CONFIG = {'rrf_k':60,'chunk_limit':3000,'top_k':150,'candidates':150,'aggregations':['best','top2mean','top3mean'],
    'selection_order':['development Recall@100','development Recall@20','development nDCG@10','lower p95 latency'],
    'reranker_sizes':[20,50],'reranker_min_ndcg_gain':0.01,'reranker_max_added_p95_seconds':1.0,
    'ann_min_destination_recall_20':0.99,'ann_min_destination_recall_100':0.98,
    'ann_p50_ratio':0.7,'ann_p95_ratio':0.8,'ann_pipeline_p95_ratio':0.9,
    'paid_api_calls':0,'embedding_api_cost':0,'reranking_api_cost':0,'paid_data_api_cost':0,'note':'No product integration. No assistant-generated new relevance grades.'}


def distribution(values):
    return {'p50':float(np.percentile(values,50)),'p95':float(np.percentile(values,95)),
        'max':float(max(values)),'mean':float(np.mean(values))} if values else {}


def split_queries(document):
    development, held_out, unjudged = [], [], []
    for query in document['queries']:
        if not query['relevance'] or query['judgment_status']=='awaiting_human_review':
            unjudged.append(query)
        elif query['split']=='held_out':
            if query['judgment_status']!='human_reviewed':
                raise ValueError('Held-out judgments require explicit human_reviewed status')
            held_out.append(query)
        else:
            if query['judgment_status'] not in {'human_reviewed','legacy','legacy_project_judgments_previously_exposed'}:
                raise ValueError('Development judgments must be human-reviewed or explicitly inherited legacy data')
            development.append(query)
    return development, held_out, unjudged


def import_human_review(worksheet, dataset):
    import csv
    document=json.loads(dataset.read_text(encoding="utf-8"))
    lookup={query["id"]:query for query in document["queries"]}
    reviewed={}
    with worksheet.open(newline="",encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            if not row["grade_0_to_3"].strip():
                continue
            key=row["query_id"]
            if key not in lookup or not row["reviewer"].strip():
                raise ValueError("Unknown query or missing human reviewer")
            if row["query"]!=lookup[key]["query"] or row["split"]!=lookup[key]["split"]:
                raise ValueError("Review cannot change frozen query text or split")
            identifier,grade=int(row["geoname_id"]),int(row["grade_0_to_3"])
            if identifier<=0 or grade not in {0,1,2,3}:
                raise ValueError("Invalid GeoNames ID or relevance grade")
            values=reviewed.setdefault(key,{})
            if identifier in values:
                raise ValueError("Duplicate query/destination judgment")
            values[identifier]={"geoname_id":identifier,"city":row["city"],"grade":grade,
                "reviewer":row["reviewer"],"evidence_url":row["evidence_revision_url"],"notes":row["notes"]}
    for key,values in reviewed.items():
        if not any(value["grade"]>0 for value in values.values()):
            raise ValueError("Each reviewed query needs at least one positive judgment")
        lookup[key]["relevance"]=list(values.values())
        lookup[key]["judgment_status"]="human_reviewed"
    save(dataset,document)
    return len(reviewed)


def metrics(queries, runs):
    if not queries:
        return {'status':'pending_human_judgments'}
    qrels=Qrels({q['id']:{str(v['geoname_id']):v['grade'] for v in q['relevance']} for q in queries})
    nonempty={q['id']:runs[q['id']] for q in queries if runs[q['id']]}
    run=Run(nonempty) if nonempty else Run()
    return {k:float(v) for k,v in evaluate(qrels,run,METRICS,make_comparable=True).items()}


def run_system(factory, queries, encoder, options, aggregation='best', reranker=None, rerank_limit=None):
    runs, per_query, timings, chunks, counts = {}, {}, {}, [], []
    for query in queries:
        wall_start=perf_counter()
        with factory() as session:
            if options.get('exact') is False:
                for setting in ['enable_seqscan=off','enable_bitmapscan=off','enable_sort=off',
                    "plan_cache_mode='force_custom_plan'",'hnsw.ef_search=400',
                    "hnsw.iterative_scan='strict_order'",'hnsw.max_scan_tuples=50000']:
                    session.execute(text('SET LOCAL '+setting))
            result=retrieve(session,RetrievalRequest(query=query['query'],top_k=150,candidates=150,rerank=reranker is not None),
                encoder,reranker,aggregation=aggregation,rerank_limit=rerank_limit,chunk_limit=CONFIG['chunk_limit'],**options)
        wall_seconds=perf_counter()-wall_start
        result['timings']['other']+=max(0.0,wall_seconds-result['timings']['total'])
        result['timings']['total']=wall_seconds
        values=result['results']
        runs[query['id']]={str(v['geoname_id']):float(len(values)-i) for i,v in enumerate(values)}
        per_query[query['id']]={'top10':[{'id':v['geoname_id'],'country':v['country_code'],'continent':v['continent']} for v in values[:10]],
            'chunk_hits':result['chunk_hits'],'destination_count':result['candidate_count'],
            'union_destination_count':result['union_destination_count'],'timings':result['timings']}
        for key,value in result['timings'].items():timings.setdefault(key,[]).append(value)
        chunks.append(sum(result['chunk_hits'].values()));counts.append(result['candidate_count'])
    return {'metrics':metrics(queries,runs),'timings':{k:distribution(v) for k,v in timings.items()},
        'candidate_chunks_processed':distribution(chunks),'destination_count':distribution(counts),
        'runs':runs,'per_query':per_query}


def save(path, value):
    temporary=path.with_suffix('.partial')
    temporary.write_text(json.dumps(value,indent=None if path.name=='experiments.json' else 2)+'\n',encoding='utf-8');temporary.replace(path)


def selection_key(system):
    m=system['metrics']
    return m['recall@100'],m['recall@20'],m['ndcg@10'],-system['timings']['total']['p95']


def analyze(factory, queries, result):
    frequencies=Counter(v['id'] for q in result['per_query'].values() for v in q['top10'])
    diversity={key:{'unique_countries':len({v['country'] for v in q['top10']}),
        'unique_continents':len({v['continent'] for v in q['top10']})} for key,q in result['per_query'].items()}
    with factory() as session:
        rows=session.execute(select(Destination.geoname_id,Destination.name,Destination.population,
            func.count(KnowledgeChunk.id),func.sum(func.length(KnowledgeChunk.text))).join(KnowledgePage,KnowledgePage.destination_id==Destination.geoname_id)
            .join(KnowledgeChunk).group_by(Destination.geoname_id)).all()
        known={row[0]:row for row in rows}
        all_ids={item['geoname_id'] for q in queries for item in q['relevance']}
        names={row[0]:row[1] for row in session.execute(select(Destination.geoname_id,Destination.name).where(Destination.geoname_id.in_(all_ids)))}
    def correlation(index):
        x=[row[index] for row in rows];y=[frequencies[row[0]] for row in rows]
        if len(set(x))<2 or len(set(y))<2:
            return {'spearman_r':None,'p_value':None,'reason':'constant input'}
        value=spearmanr(x,y)
        return {'spearman_r':float(value.statistic) if np.isfinite(value.statistic) else None,
            'p_value':float(value.pvalue) if np.isfinite(value.pvalue) else None}
    errors=[]
    for query in queries:
        ranking=list(result['runs'][query['id']])
        expected=query['relevance']
        missing=[item for item in expected if str(item['geoname_id']) not in ranking[:20]]
        if missing:
            errors.append({'query_id':query['id'],'query':query['query'],'misses':[{
                'id':item['geoname_id'],'city':names.get(item['geoname_id'],item.get('city')),
                'rank':ranking.index(str(item['geoname_id']))+1 if str(item['geoname_id']) in ranking else None,
                'knowledge_chunks':known[item['geoname_id']][3] if item['geoname_id'] in known else 0,
                'classification':'coverage_or_identity_gap' if item['geoname_id'] not in known else 'ranking_or_judgment_mismatch_requires_evidence_review'} for item in missing]})
    return {'per_query_diversity':diversity,'country_diversity':distribution([v['unique_countries'] for v in diversity.values()]),
        'continent_diversity':distribution([v['unique_continents'] for v in diversity.values()]),
        'most_frequent_destinations':[{'id':key,'city':known[key][1],'top10_appearances':count} for key,count in frequencies.most_common(15)],
        'top10_concentration_top5':sum(count for _,count in frequencies.most_common(5))/max(1,sum(frequencies.values())),
        'popularity_correlations':{'population':correlation(2),'chunk_count':correlation(3),'text_characters':correlation(4)},
        'correlation_note':'All corpus cities included, zero appearances retained. Correlation is descriptive, not causation or corrected relevance.','weak_queries':errors}


def benchmark(factory, registry, dataset, output):
    document=json.loads(dataset.read_text(encoding='utf-8'))
    split_path=dataset.parent/'splits.json'
    if split_path.exists():
        split=json.loads(split_path.read_text(encoding='utf-8'))
        definitions=[{k:q[k] for k in ['id','query','split']} for q in document['queries']]
        if sha256(json.dumps(definitions,sort_keys=True).encode()).hexdigest()!=split.get('query_definitions_sha256'):
            raise ValueError('Frozen query text or split changed')
    development, held_out, unjudged=split_queries(document)
    if not development:raise ValueError('No reviewed/legacy development qrels available')
    output.mkdir(parents=True,exist_ok=True)
    path=output/'experiments.json'
    corpus_digest=sha256()
    with factory() as session:
        for identifier,content_hash in session.execute(select(KnowledgeChunk.id,KnowledgeChunk.content_hash).order_by(KnowledgeChunk.id).execution_options(yield_per=2048)):
            corpus_digest.update(f"{identifier}:{content_hash}\n".encode())
    implementation_hash=sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in ['full_evaluate.py','full_ann.py','service.py','embedding.py'])).hexdigest()
    fingerprint=sha256((implementation_hash+dataset.read_text(encoding='utf-8')+json.dumps(CONFIG,sort_keys=True)+corpus_digest.hexdigest()+
        json.dumps({k:v['revision'] for k,v in registry.items()},sort_keys=True)).encode()).hexdigest()
    report=json.loads(path.read_text()) if path.exists() else {'fingerprint':fingerprint,'config':CONFIG,'systems':{}}
    if report['fingerprint']!=fingerprint:raise ValueError('Dataset/config changed; use a new versioned output directory')
    report['implementation_sha256']=implementation_hash
    report['environment']={'platform':platform.platform(),'processor':platform.processor(),'query_threads':4,
        'packages':{name:version(name) for name in ['sentence-transformers','torch','numpy','ranx','pgvector']}}
    report['judgments']={'development':len(development),'held_out':len(held_out),'unjudged':len(unjudged),'warning':'Legacy development qrels are provisional project judgments, already exposed in Milestone4A. No independent quality claim.'}
    with factory() as session:
        report['corpus']={'chunks':session.scalar(select(func.count()).select_from(KnowledgeChunk)),
            'destinations':session.scalar(select(func.count(func.distinct(KnowledgePage.destination_id)))),
            'source_hashes':list(session.scalars(select(KnowledgePage.dump_sha256).distinct())), 'chunk_fingerprint':corpus_digest.hexdigest()}
    if not report['corpus']['chunks']:
        raise ValueError('No usable knowledge chunks for evaluation')
    report['embedding_coverage']={}
    for name in MODELS:
        if name not in registry:raise ValueError(f'Missing embedding model registry entry: {name}')
        status=embedding_status(factory,name,registry[name]['revision'])
        report['embedding_coverage'][name]=status
        if status['remaining']:raise ValueError(f'Full-corpus embedding incomplete for {name}: {status["remaining"]} chunks remaining')
    if any(name not in registry for name in [RERANKER,SMALL_RERANKER]):
        raise ValueError('Missing reranker registry entry; use download-models --include-small-reranker')
    report['reranker_models']={name:{k:v for k,v in registry[name].items() if k!='path'} for name in [RERANKER,SMALL_RERANKER]}
    for name in MODELS:
        data=registry[name]
        load_start=perf_counter()
        encoder=LocalEncoder(name,data['revision'],data['path'])
        report.setdefault('model_load_seconds',{})[name]=perf_counter()-load_start
        # Warm model/database/JIT separately from measured query systems.
        before=perf_counter()
        with factory() as session:
            retrieve(session,RetrievalRequest(query=development[0]['query'],rerank=False),encoder)
        report.setdefault('startup_seconds',{})[name]=perf_counter()-before
        report.setdefault('models',{})[name]={k:v for k,v in data.items() if k!='path'}
        for aggregation in CONFIG['aggregations']:
            for mode,options in MODES.items():
                key=f'{name}:{mode}:{aggregation}'
                if key not in report['systems']:
                    report['systems'][key]=run_system(factory,development,encoder,options,aggregation)
                    save(path,report);print(key,report['systems'][key]['metrics'],flush=True)
        del encoder
    chosen=max((key for key in report['systems'] if ':rerank:' not in key),key=lambda key:selection_key(report['systems'][key]))
    dense_choice=max((key for key in report['systems'] if ':rerank:' not in key and key.split(':')[1]!='lexical'),key=lambda key:selection_key(report['systems'][key]))
    report['embedding_model_recommendation']={'model':dense_choice.split(':')[0],'development_system':dense_choice,'rule':CONFIG['selection_order']}
    report['selection']={'system':chosen,'rule':CONFIG['selection_order'],'held_out_accessed_for_selection':False}
    name,mode,aggregation=chosen.split(':')
    if mode=='lexical':name=report['embedding_model_recommendation']['model']
    data=registry[name];encoder=LocalEncoder(name,data['revision'],data['path'])
    baseline=report['systems'][chosen]
    for reranker_name in [RERANKER,SMALL_RERANKER]:
        data=registry[reranker_name];reranker=LocalReranker(data['path'],data['revision'])
        reranker.score(development[0]['query'],['Historic streets and museums.'])
        for limit in CONFIG['reranker_sizes']:
            key=f'{chosen}:rerank:{reranker_name}:{limit}'
            if key not in report['systems']:
                report['systems'][key]=run_system(factory,development,encoder,MODES[mode],aggregation,reranker,limit)
                save(path,report);print(key,report['systems'][key]['metrics'],flush=True)
        del reranker
    eligible=[key for key,value in report['systems'].items() if ':rerank:' in key and
        value['metrics']['ndcg@10']>=baseline['metrics']['ndcg@10']+CONFIG['reranker_min_ndcg_gain'] and
        value['timings']['total']['p95']<=baseline['timings']['total']['p95']+CONFIG['reranker_max_added_p95_seconds']]
    selected=max(eligible,key=lambda key:report['systems'][key]['metrics']['ndcg@10']) if eligible else chosen
    report['selection']['final_system']=selected
    report['analysis']=analyze(factory,development,report['systems'][selected])
    # No held-out judgments are used until every development choice is frozen.
    save(path,report)
    report['held_out']={'status':'pending_human_judgments'}
    if held_out:
        reranker=None;limit=None
        if ':rerank:' in selected:
            _,reranker_name,limit=selected.split(':rerank:')[0],selected.split(':rerank:')[1].rsplit(':',1)[0],int(selected.rsplit(':',1)[1])
            data=registry[reranker_name];reranker=LocalReranker(data['path'],data['revision'])
        report['held_out']=run_system(factory,held_out,encoder,MODES[mode],aggregation,reranker,limit)
        report['held_out_analysis']=analyze(factory,held_out,report['held_out'])
    if 'ann' not in report:
        from app.retrieval.full_ann import compare_ann
        selected_reranker=None;selected_limit=None
        if ':rerank:' in selected:
            reranker_name,limit_text=selected.split(':rerank:')[1].rsplit(':',1)
            selected_limit=int(limit_text);reranker_data=registry[reranker_name]
            selected_reranker=LocalReranker(reranker_data['path'],reranker_data['revision'])
        report['ann']=compare_ann(factory,development,encoder,MODES[mode],aggregation,selected_reranker,selected_limit)
    ann=report['ann']
    if 'database_seconds' in ann:
        exact_time=ann['database_seconds']['exact'];ann_time=ann['database_seconds']['hnsw']
        recall=ann['recall_against_exact']['destinations']
        report['ann']['material_gain_with_recall']=bool(ann['hnsw_index_used'] and recall['20']>=CONFIG['ann_min_destination_recall_20'] and recall['100']>=CONFIG['ann_min_destination_recall_100'] and ann_time['p50']<=CONFIG['ann_p50_ratio']*exact_time['p50'] and ann_time['p95']<=CONFIG['ann_p95_ratio']*exact_time['p95'])
    pipeline=ann.get('pipeline_hnsw')
    ann_acceptable=bool(mode!='lexical' and ann.get('material_gain_with_recall') and pipeline and
        pipeline['metrics']['recall@100']>=report['systems'][selected]['metrics']['recall@100']-0.01 and
        pipeline['metrics']['ndcg@10']>=report['systems'][selected]['metrics']['ndcg@10']-0.01 and
        pipeline['timings']['total']['p95']<=CONFIG['ann_pipeline_p95_ratio']*report['systems'][selected]['timings']['total']['p95'])
    observed=pipeline if ann_acceptable else report['systems'][selected]
    report['recommended_architecture']={'embedding_model':name,'revision':registry[name]['revision'],
        'vector_search':'not_used' if mode=='lexical' else 'hnsw' if ann_acceptable else 'exact_cosine',
        'dense_k':MODES[mode].get('dense_k',0),'lexical_k':MODES[mode].get('lexical_k',0),
        'strategy':mode,'aggregation':aggregation,'reranking_enabled':':rerank:' in selected,
        'reranker_candidates':int(selected.rsplit(':',1)[1]) if ':rerank:' in selected else 0,
        'reranker_model':selected.split(':rerank:')[1].rsplit(':',1)[0] if ':rerank:' in selected else None,
        'final_shortlist':20,'observed_seconds':observed['timings']['total'],
        'status':'provisional development recommendation; human-reviewed held-out validation pending' if not held_out else 'development-selected, held-out reported separately',
        'production_integration':False,'ann_index_provisioning_required':ann_acceptable}
    save(path,report)
    save(output/'recommended-architecture.json',report['recommended_architecture'])
    write_summary(report,output/'summary.md')
    return report


def write_summary(report,path):
    lines=['# Full-corpus retrieval engineering evaluation','',json.dumps(report['corpus']), '',json.dumps(report['judgments']),
        '', '**Held-out status:** '+str(report['held_out'].get('status','evaluated after development selection')),
        '', '| Development system | R@20 | R@50 | R@100 | MRR | nDCG@5 | nDCG@10 | p50/p95/max s |',
        '|---|---:|---:|---:|---:|---:|---:|---|']
    for key,value in report['systems'].items():
        m=value['metrics'];t=value['timings']['total']
        lines.append(f"| {key} | {m['recall@20']:.4f} | {m['recall@50']:.4f} | {m['recall@100']:.4f} | {m['mrr']:.4f} | {m['ndcg@5']:.4f} | {m['ndcg@10']:.4f} | {t['p50']:.3f}/{t['p95']:.3f}/{t['max']:.3f} |")
    lines+=['','Selection: `'+json.dumps(report['selection'])+'`', '', 'All retrieval is full-corpus; no benchmark/qrel destinations were removed. Quality findings remain provisional until human-reviewed held-out evaluation. No product scoring or frontend changes. No paid APIs.']
    path.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    save(path.parent/'metrics.json',{key:value['metrics'] for key,value in report['systems'].items()})
    save(path.parent/'error-analysis.json',report['analysis'])
    save(path.parent/'latency.json',{key:value['timings'] for key,value in report['systems'].items()})
    save(path.parent/'configuration.json',dict(report['config'],models=report.get('models',{}),reranker_models=report.get('reranker_models',{}),fingerprint=report['fingerprint'],judgments=report['judgments']))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--registry',type=Path,default=Path('.tmp/models/registry.json'))
    parser.add_argument('--dataset',type=Path,default=Path('evaluation/retrieval/full-corpus-v1/queries-review.json'))
    parser.add_argument('--output',type=Path,default=Path('evaluation/retrieval/full-corpus-v1'))
    parser.add_argument('--import-review',type=Path,help='Import explicitly human-authored worksheet grades; no inference')
    args=parser.parse_args()
    if args.import_review:
        print('Reviewed queries imported:',import_human_review(args.import_review,args.dataset));return
    import numba
    numba.set_num_threads(4)
    engine=create_engine(str(Settings().database_url))
    try:benchmark(sessionmaker(engine,expire_on_commit=False),json.loads(args.registry.read_text()),args.dataset,args.output)
    finally:engine.dispose()


if __name__=='__main__':main()
