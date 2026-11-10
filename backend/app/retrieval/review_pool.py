"""Build an ungraded multi-system destination pool for human relevance review."""
import argparse
import csv
from collections import Counter
from datetime import datetime,timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import statistics
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import sessionmaker
from app.core.config import Settings
from app.knowledge.models import KnowledgeChunk,KnowledgePage
from app.retrieval.embedding import LocalEncoder,embedding_status
from app.retrieval.service import RetrievalRequest,retrieve

MODEL='BAAI/bge-small-en-v1.5'
REVISION='5c38ec7c405ec4b44b94cc5a9bb96e735b38267a'
DEPTH=20
SYSTEMS={'lexical':'lexical','dense':'dense','hybrid':'hybrid'}
ORDER_SEED='roamline-human-review-pool-v1'
FIELDS=['query_id','query','split','geoname_id','city','country','country_code',
    'grade_0_to_3','evidence_revision_url','reviewer','notes']


def merge_candidates(rankings):
    candidates={}
    for system,results in rankings.items():
        for rank,result in enumerate(results,1):
            identifier=result['geoname_id']
            identity={key:result[key] for key in ['geoname_id','city','country','country_code','continent']}
            item=candidates.setdefault(identifier,dict(identity,systems={},evidence={}))
            if any(item[key]!=value for key,value in identity.items()):
                raise ValueError('Canonical destination metadata changed between retrieval systems')
            if system in item['systems']:
                raise ValueError('Duplicate destination within one retrieval system')
            item['systems'][system]={'rank':rank,'retrieval_score':result['retrieval_score'],
                'dense_rank':result.get('dense_rank'),'lexical_rank':result.get('lexical_rank')}
            for evidence in result['evidence']:
                item['evidence'].setdefault(evidence['chunk_id'],dict(evidence))
    for item in candidates.values():
        item['evidence']=sorted(item['evidence'].values(),key=lambda e:(e['page_id'],e['chunk_id']))
    return candidates


def shuffled_ids(query_id,candidates):
    return sorted(candidates,key=lambda identifier:sha256(f'{ORDER_SEED}|{query_id}|{identifier}'.encode()).digest())


def review_rows(query,candidates):
    rows=[]
    for identifier in shuffled_ids(query['id'],candidates):
        item=candidates[identifier]
        evidence=next((entry for entry in item['evidence'] if entry.get('url')),None)
        rows.append({'query_id':query['id'],'query':query['query'],'split':query['split'],
            'geoname_id':identifier,'city':item['city'],'country':item['country'],'country_code':item['country_code'],
            'grade_0_to_3':'','evidence_revision_url':evidence['url'] if evidence else '', 'reviewer':'','notes':''})
    return rows


def assert_ungraded(raw):
    records=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    for row in records:
        if row.get('grade_0_to_3','').strip() or row.get('reviewer','').strip():
            raise ValueError('Refusing to replace human judgments or reviewer entries')
        note=row.get('notes','').strip()
        if note and note!='Add multiple destination rows per query; leave grade blank until reviewed.':
            raise ValueError('Refusing to replace manual reviewer comments')
    return records


def build_pool(factory,encoder,directory):
    if encoder.name!=MODEL or encoder.revision!=REVISION:
        raise ValueError('Use the validated pinned BGE model')
    status=embedding_status(factory,MODEL,REVISION)
    if not status['total_chunks'] or status['remaining']:
        raise ValueError('Full BGE corpus must be complete before candidate pooling')
    worksheet=directory/'human-review.csv';original=worksheet.read_bytes();current_rows=assert_ungraded(original)
    document=json.loads((directory/'queries-review.json').read_text(encoding='utf-8'))
    queries=[query for query in document['queries'] if query['id'].startswith('review-')]
    if len(queries)!=24 or {row['query_id'] for row in current_rows}!={query['id'] for query in queries}:
        raise ValueError('Expected exactly the 24 existing review queries')
    definitions=[{key:query[key] for key in ['id','query','split']} for query in document['queries']]
    splits=json.loads((directory/'splits.json').read_text(encoding='utf-8'))
    if sha256(json.dumps(definitions,sort_keys=True).encode()).hexdigest()!=splits['query_definitions_sha256']:
        raise ValueError('Frozen query definitions or split assignments changed')
    lookup={q['id']:q for q in queries}
    if any(row['query']!=lookup[row['query_id']]['query'] or row['split']!=lookup[row['query_id']]['split'] for row in current_rows):
        raise ValueError('Worksheet query text/split mismatch')
    backup=directory/'human-review-template.csv'
    if not backup.exists():
        backup.write_bytes(original)
    corpus_digest=sha256()
    with factory() as session:
        for identifier,digest in session.execute(select(KnowledgeChunk.id,KnowledgeChunk.content_hash).order_by(KnowledgeChunk.id).execution_options(yield_per=2048)):
            corpus_digest.update(f'{identifier}:{digest}\n'.encode())
        sources=list(session.scalars(select(KnowledgePage.dump_sha256).distinct()))
    provenance={'created_at':datetime.now(timezone.utc).isoformat(),'model':MODEL,'revision':REVISION,
        'corpus':status,'corpus_chunk_fingerprint':corpus_digest.hexdigest(),'source_dump_hashes':sources,
        'configuration':{'depth_per_system':DEPTH,'candidate_destinations':50,'chunk_limit_per_channel':3000,
            'aggregation':'best','rrf_k':60,'filters':None,'reranker':False,'query_format':'Represent this sentence for searching relevant passages: <query>',
            'row_order':f'SHA256({ORDER_SEED}|query_id|geoname_id), ascending digest',
            'csv_evidence_choice':'Lowest source page ID then chunk ID with an available stored URL'},
        'query_definitions_sha256':splits['query_definitions_sha256'],'template_sha256':sha256(backup.read_bytes()).hexdigest(),
        'code_sha256':sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in ['review_pool.py','service.py','embedding.py'])).hexdigest(),
        'note':'Candidate retrieval only, no evaluation metrics, tuning, relevance grades, generated evidence or product integration.',
        'queries':[]}
    rows=[];memberships=Counter();exclusive=Counter();shared=0;all_destinations={};country_pairs=Counter();continent_pairs=Counter()
    for query in queries:
        vector=encoder.encode([query['query']],query=True)[0].tolist()
        rankings={}
        for system,mode in SYSTEMS.items():
            with factory() as session:
                result=retrieve(session,RetrievalRequest(query=query['query'],top_k=DEPTH,candidates=50,rerank=False),
                    None if mode=='lexical' else encoder,mode=mode,query_vector=vector if mode!='lexical' else None,
                    aggregation='best',chunk_limit=3000)
            rankings[system]=result['results']
        candidates=merge_candidates(rankings);rows.extend(review_rows(query,candidates))
        for item in candidates.values():
            memberships.update(item['systems'].keys())
            if len(item['systems'])==1:exclusive.update(item['systems'].keys())
            else:shared+=1
            all_destinations[item['geoname_id']]=item
            country_pairs[item['country_code']]+=1;continent_pairs[item['continent']]+=1
        provenance['queries'].append({'id':query['id'],'query':query['query'],'split':query['split'],
            'system_top_ids':{key:[value['geoname_id'] for value in results] for key,results in rankings.items()},
            'csv_candidate_order':shuffled_ids(query['id'],candidates),'candidates':list(candidates.values())})
        print(json.dumps({'query_id':query['id'],'pooled_candidates':len(candidates)}),flush=True)
    counts=[len(query['candidates']) for query in provenance['queries']]
    summary={'queries':len(queries),'candidates_per_query':{'min':min(counts),'median':statistics.median(counts),'max':max(counts)},
        'query_destination_pairs':len(rows),'unique_destinations':len(all_destinations),
        'system_memberships':dict(memberships),'system_exclusive_pairs':dict(exclusive),'shared_by_multiple_systems':shared,
        'country_pair_counts':dict(sorted(country_pairs.items())),'continent_pair_counts':dict(sorted(continent_pairs.items())),
        'unique_countries':len(country_pairs),'unique_continents':len(continent_pairs),
        'country_destination_counts':dict(sorted(Counter(v['country_code'] for v in all_destinations.values()).items())),
        'continent_destination_counts':dict(sorted(Counter(v['continent'] for v in all_destinations.values()).items())),
        'missing_evidence_urls':sum(not row['evidence_revision_url'] for row in rows),'graded_rows':0,'reviewer_entries':0}
    assert len({(row['query_id'],row['geoname_id']) for row in rows})==len(rows)
    buffer=io.StringIO(newline='');writer=csv.DictWriter(buffer,fieldnames=FIELDS);writer.writeheader();writer.writerows(rows)
    csv_bytes=buffer.getvalue().encode('utf-8');provenance['output_csv_sha256']=sha256(csv_bytes).hexdigest();provenance['summary']=summary
    if worksheet.read_bytes()!=original:
        raise ValueError('Worksheet changed while building the pool; refusing to overwrite it')
    temporary=worksheet.with_suffix('.partial');temporary.write_bytes(csv_bytes);temporary.replace(worksheet)
    sidecar=directory/'human-review-pool.json';temporary=sidecar.with_suffix('.partial')
    temporary.write_text(json.dumps(provenance,indent=2)+'\n',encoding='utf-8');temporary.replace(sidecar)
    (directory/'human-review-pool-summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    return summary


def main():
    parser=argparse.ArgumentParser(description='Ungraded multi-system human review pool; requires complete BGE corpus')
    parser.add_argument('--model-path',type=Path,required=True)
    parser.add_argument('--directory',type=Path,default=Path('evaluation/retrieval/full-corpus-v1'))
    args=parser.parse_args();engine=create_engine(str(Settings().database_url))
    try:
        encoder=LocalEncoder(MODEL,REVISION,args.model_path,threads=2,inference_batch_size=16)
        print(json.dumps(build_pool(sessionmaker(engine,expire_on_commit=False),encoder,args.directory),indent=2))
    finally:engine.dispose()


if __name__=='__main__':main()
