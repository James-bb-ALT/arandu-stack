"""Independent validator for prepared entity-resolution evidence."""
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
BASE=ROOT/'evaluation'/'ptbr-independent'/'entity-resolution'
CATALOG=json.loads((BASE/'catalog-v1.json').read_text(encoding='utf-8'))
SPEC=json.loads((BASE/'spec-v1.json').read_text(encoding='utf-8'))

ALLOWED={'resolved','ambiguous','no_match'}

def fail(message): raise ValueError(message)
def validate():
    if SPEC['status']!='prepared_not_implemented': fail('spec status changed')
    entities=CATALOG['entities']; ids=[e['registry_id'] for e in entities]; dotted=[e['entity_id'] for e in entities]
    if len(ids)!=len(set(ids)) or len(dotted)!=len(set(dotted)): fail('duplicate catalog identity')
    areas={a['area_id'] for a in CATALOG['areas']}
    for e in entities:
        if e['area_id'] not in areas: fail('unknown area')
        if not e['display_name'] or not isinstance(e['aliases'],list): fail('invalid entity names')
    rows=[]
    for line in (BASE/'corpus-v1.jsonl').read_text(encoding='utf-8').splitlines():
        row=json.loads(line); expected=row['expected']; outcome=expected['outcome']
        if not row['mention'] and row['span'] is not None: fail(f"{row['id']}: empty mention span")
        if row['span'] is not None:
            start, end = row['span']
            encoded = row['text'].encode('utf-8')
            invalid_bounds = start < 0 or end <= start or end > len(encoded)
            if invalid_bounds:
                if outcome != 'no_match': fail(f"{row['id']}: invalid span must abstain")
            else:
                try: mention = encoded[start:end].decode('utf-8')
                except UnicodeDecodeError:
                    if outcome != 'no_match': fail(f"{row['id']}: invalid UTF-8 span must abstain")
                else:
                    if mention != row['mention'] and outcome != 'no_match': fail(f"{row['id']}: span does not cover mention")
        variant = row.get('catalog_variant')
        if variant not in (None, 'duplicate_registry_id'):
            fail(f"{row['id']}: unknown catalog variant")
        if variant == 'duplicate_registry_id' and row['id'] != 'er-duplicate-id-candidate':
            fail(f"{row['id']}: invalid duplicate-id fixture")
        if row['id'] == 'er-capability-conflict' and row['constraints'].get('capability') != 'set_fan_percentage':
            fail(f"{row['id']}: missing capability constraint")
        if outcome not in ALLOWED: fail(f"{row['id']}: invalid outcome")
        if row['catalog_id']!=CATALOG['catalog_id']: fail(f"{row['id']}: catalog mismatch")
        if not isinstance(row['text'],str) or not isinstance(row['mention'],str): fail(f"{row['id']}: text type")
        if row['generation'] not in {'gen-001','gen-000'}: fail(f"{row['id']}: generation")
        if outcome=='resolved' and (len(expected.get('candidates',[]))!=1 or 'evidence' not in expected): fail(f"{row['id']}: resolved shape")
        if outcome=='ambiguous' and len(expected.get('candidates',[]))<2: fail(f"{row['id']}: ambiguous shape")
        if outcome=='no_match' and ('candidates' in expected or 'evidence' in expected): fail(f"{row['id']}: no_match must not expose candidate")
        if row['span'] is not None and (not isinstance(row['span'],list) or len(row['span'])!=2 or any(not isinstance(v,int) for v in row['span'])): fail(f"{row['id']}: invalid span shape")
        rows.append(row)
    ids=[r['id'] for r in rows]
    if len(ids)!=len(set(ids)): fail('duplicate case id')
    required={'precedence','area','ambiguity','match','generation','constraint','span','determinism','malformed','catalog','capability'}
    dimensions={d for r in rows for d in r['dimensions']}
    if not required.issubset(dimensions): fail('coverage dimensions incomplete')
    return rows
if __name__=='__main__':
    rows=validate(); print(json.dumps({'cases':len(rows),'outcomes':{k:sum(r['expected']['outcome']==k for r in rows) for k in sorted(ALLOWED)}},sort_keys=True))
