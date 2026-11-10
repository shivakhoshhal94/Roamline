"""Pool construction never assigns judgments and protects manual edits."""
import csv
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
import numpy as np
import pytest
from app.retrieval.review_pool import MODEL,REVISION,merge_candidates,shuffled_ids,review_rows,assert_ungraded,build_pool


def destination(identifier):
    return {'geoname_id':identifier,'city':f'City {identifier}','country':{1:'Italy',2:'Japan',3:'Brazil'}[identifier],
        'country_code':{1:'IT',2:'JP',3:'BR'}[identifier],'continent':{1:'EU',2:'AS',3:'SA'}[identifier],
        'retrieval_score':1.0,'rank':identifier,'dense_rank':identifier,'lexical_rank':identifier,
        'evidence':[{'chunk_id':identifier,'page_id':identifier,'revision_id':100+identifier,
            'url':f'https://en.wikivoyage.org/w/index.php?oldid={100+identifier}','snippet':'Stored test fixture text.'}]}


def test_merge_deduplicates_and_keeps_all_system_provenance():
    values=merge_candidates({'lexical':[destination(1),destination(2)],'dense':[destination(2),destination(3)],'hybrid':[destination(3),destination(1)]})
    assert set(values)=={1,2,3}
    assert all(len(item['systems'])==2 for item in values.values())
    assert values[1]['systems']['lexical']['rank']==1
    assert len(values[1]['evidence'])==1
    assert shuffled_ids('query',values)==shuffled_ids('query',dict(reversed(list(values.items()))))
    rows=review_rows({'id':'query','query':'Historic streets','split':'held_out'},values)
    assert all(row['split']=='held_out' for row in rows)
    assert all(row['grade_0_to_3']==row['reviewer']==row['notes']=='' for row in rows)
    assert not any('score' in column or 'rank' in column for column in rows[0])


def test_duplicate_system_candidate_is_rejected():
    with pytest.raises(ValueError,match='Duplicate destination'):
        merge_candidates({'dense':[destination(1),destination(1)]})


@pytest.mark.parametrize('column,value',[('grade_0_to_3','0'),('grade_0_to_3','3'),('reviewer','Human reviewer'),('notes','Manual comment')])
def test_manual_review_values_are_never_overwritten(column,value):
    import io
    data=io.StringIO();writer=csv.DictWriter(data,fieldnames=['grade_0_to_3','reviewer','notes']);writer.writeheader();writer.writerow({column:value})
    with pytest.raises(ValueError,match='Refusing to replace'):
        assert_ungraded(data.getvalue().encode())


def test_build_preserves_template_splits_and_outputs_only_ungraded_rows(tmp_path):
    root=Path(__file__).resolve().parents[2]/'evaluation/retrieval/full-corpus-v1'
    for name in ['queries-review.json','splits.json']:
        shutil.copyfile(root/name,tmp_path/name)
    prompts=json.loads((tmp_path/'queries-review.json').read_text())['queries']
    fields=['query_id','query','split','geoname_id','city','grade_0_to_3','evidence_revision_url','reviewer','notes']
    with (tmp_path/'human-review.csv').open('w',newline='',encoding='utf-8') as file:
        writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader()
        writer.writerows({'query_id':q['id'],'query':q['query'],'split':q['split']} for q in prompts if q['id'].startswith('review-'))
    before=(tmp_path/'human-review.csv').read_bytes()
    factory=MagicMock();session=factory.return_value.__enter__.return_value
    session.execute.return_value=[];session.scalars.return_value=[]
    encoder=SimpleNamespace(name=MODEL,revision=REVISION,encode=lambda *args,**kwargs:np.ones((1,384),dtype=np.float32))
    def retrieve(session,request,*args,mode,**kwargs):
        assert not request.rerank and request.top_k==20 and kwargs['chunk_limit']==3000
        ids={'lexical':[1,2],'dense':[2,3],'hybrid':[3,1]}[mode]
        return {'results':[destination(identifier) for identifier in ids]}
    with patch('app.retrieval.review_pool.embedding_status',return_value={'total_chunks':162610,'remaining':0}),patch('app.retrieval.review_pool.retrieve',side_effect=retrieve):
        summary=build_pool(factory,encoder,tmp_path)
    assert summary['queries']==24 and summary['query_destination_pairs']==72
    assert summary['unique_destinations']==3 and summary['shared_by_multiple_systems']==72
    assert summary['system_memberships']=={'lexical':48,'dense':48,'hybrid':48}
    assert summary['missing_evidence_urls']==0
    assert (tmp_path/'human-review-template.csv').read_bytes()==before
    with (tmp_path/'human-review.csv').open(newline='',encoding='utf-8') as f:rows=list(csv.DictReader(f))
    assert len({(row['query_id'],row['geoname_id']) for row in rows})==72
    assert all(row['grade_0_to_3']==row['reviewer']==row['notes']=='' for row in rows)
    assert (tmp_path/'human-review-pool.json').exists()
