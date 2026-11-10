# Reproducing the ungraded review artifacts

The stored pool was generated before the product integration's narrow-ID hydration
and destination-filter changes. Its `code_sha256` is intentionally historical:
`626cb330c41374599d59ba63b3f6f4739d9273235be8f15a8fab05cc763b2437`.
Do not replace it with a current-source checksum or regenerate the pool merely to
make that checksum match. The generation source can be reconstructed from the
source files in this evaluation-tooling commit and the preceding embedding commit
`2f26be9fbcf857a2cb4adab788622f3deffa9035`.

Run this read-only Python check from the repository root, with this commit's
versions of `review_pool.py`, `service.py` and `embedding.py` checked out. It neither
loads weights nor queries the database or a provider. Original source bytes used
Windows CRLF line endings, so the fingerprint explicitly restores those endings.

```python
import ast
import hashlib
import json
from pathlib import Path
import subprocess

root = Path('backend/app/retrieval')
service = (root / 'service.py').read_text(encoding='utf-8')
base = subprocess.check_output([
    'git', 'show',
    '2f26be9fbcf857a2cb4adab788622f3deffa9035:backend/app/retrieval/service.py',
]).decode('utf-8').replace('\r\n', '\n')

def function(source, name):
    node = next(n for n in ast.parse(source).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    return ''.join(source.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])

for name in ['filtered', 'chunk_ranks']:
    service = service.replace(function(service, name), function(base, name))
service = service.replace(
    '    destination_id: int | None = Field(default=None, gt=0)\n', '')
texts = [(root / 'review_pool.py').read_text(encoding='utf-8'), service,
         (root / 'embedding.py').read_text(encoding='utf-8')]
checksum = hashlib.sha256(b''.join(
    source.replace('\n', '\r\n').encode('utf-8') for source in texts
)).hexdigest()
provenance = json.loads(Path(
    'evaluation/retrieval/full-corpus-v1/human-review-pool.json'
).read_text(encoding='utf-8'))
assert checksum == provenance['code_sha256']
print('Historical generation source checksum verified')
```

The original CSV is also reproducible directly from the stored candidate pool,
without rerunning retrieval, assigning a relevance label or writing any files.
With the backend importable (for example `PYTHONPATH=backend`):

```python
import csv
import hashlib
import io
import json
from pathlib import Path
from app.retrieval.review_pool import FIELDS, review_rows, shuffled_ids

root = Path('evaluation/retrieval/full-corpus-v1')
pool = json.loads((root / 'human-review-pool.json').read_text(encoding='utf-8'))
rows = []
for query in pool['queries']:
    candidates = {item['geoname_id']: item for item in query['candidates']}
    assert shuffled_ids(query['id'], candidates) == query['csv_candidate_order']
    rows.extend(review_rows(query, candidates))
assert len(rows) == len({(r['query_id'], r['geoname_id']) for r in rows}) == 1036
assert all(r['grade_0_to_3'] == r['reviewer'] == r['notes'] == '' for r in rows)
output = io.StringIO(newline='')
writer = csv.DictWriter(output, fieldnames=FIELDS)
writer.writeheader()
writer.writerows(rows)
original = output.getvalue().encode('utf-8')
assert original == (root / 'human-review.csv').read_bytes()
assert hashlib.sha256(original).hexdigest() == pool['output_csv_sha256']
print('Original ungraded worksheet reproduced byte-for-byte')
```

These checks describe the original **ungraded** worksheet. Subsequent deliberate
human grading changes its content/hash; preserve the original archive/provenance
and track reviewed output separately. The template backup is not the worksheet to
grade. Stored JSON retains retrieval ranks/scores separately from grading columns.
No pool membership, ANN overlap target or test-fixture grade is a human relevance
judgment. Human-reviewed held-out results remain pending.

Repository attributes disable line-ending conversion only for the three checksum-bearing inputs: `human-review.csv`, `human-review-template.csv` and `queries-review.json`. Their original bytes (including CRLF) are preserved on all platforms. These are ordinary text artifacts, not model binaries. Other files retain the repository normal line-ending behavior.
