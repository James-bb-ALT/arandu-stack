"""Create and verify immutable entity-resolution input freeze."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
BASE=ROOT/'evaluation'/'ptbr-independent'/'entity-resolution'
MANIFEST=BASE/'freeze-v4.json'
FILES=['spec-v1.json','catalog-v1.json','corpus-v1.jsonl','generate.py','oracle.py','freeze.py','prebaseline.py']
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def build():
    rows=[json.loads(line) for line in (BASE/'corpus-v1.jsonl').read_text(encoding='utf-8').splitlines() if line]
    return {'schema_version':1,'freeze_id':'ptbr-independent-entity-resolution-v4','purpose':'prepared entity-resolution evidence before product implementation','provenance':{'kind':'PROJECT_AUTHORED_SYNTHETIC','license':'Apache-2.0','product_output_used_for_labels':False},'files':{name:{'path':f'evaluation/ptbr-independent/entity-resolution/{name}','sha256':digest(BASE/name)} for name in FILES},'corpus':{'line_count':len(rows),'outcomes':{k:sum(r['expected']['outcome']==k for r in rows) for k in ('ambiguous','no_match','resolved')}}}
def main():
    p=argparse.ArgumentParser(); p.add_argument('--check',action='store_true'); p.add_argument('--replace',action='store_true'); a=p.parse_args(); value=build()
    if a.check:
        if not MANIFEST.exists() or json.loads(MANIFEST.read_text(encoding='utf-8'))!=value: raise SystemExit('entity-resolution freeze drift')
        print('entity-resolution freeze verified'); return
    if MANIFEST.exists() and not a.replace: raise SystemExit('refusing to overwrite entity-resolution freeze')
    MANIFEST.write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8',newline='\n'); print(f'entity-resolution freeze created: {MANIFEST}')
if __name__=='__main__': main()
