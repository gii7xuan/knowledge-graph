"""Lossless wire compaction for bounded corrections; never truncate source material."""
from copy import deepcopy
import json
from collections import Counter
import re


def record_table(records):
    """Remove repeated keys only, preserving every value and record order."""
    if not records or not all(isinstance(r, dict) for r in records):
        return deepcopy(records)
    keys = list(dict.fromkeys(k for r in records for k in r))
    common = {k:deepcopy(records[0][k]) for k in keys if all(k in r for r in records)
              and all(r[k] == records[0][k] for r in records)}
    columns = [k for k in keys if k not in common]
    result = {'encoding': 'record_table_v1', 'common':common, 'columns': columns,
              'rows': [[deepcopy(r.get(k)) for k in columns] for r in records]}
    absent = {str(i):[k for k in columns if k not in r] for i,r in enumerate(records) if any(k not in r for k in columns)}
    if absent:result['absent']=absent
    return result


def expand_table(value):
    if isinstance(value, dict) and value.get('encoding') == 'record_table_v1':
        result=[]
        for i,row in enumerate(value['rows']):
            record={**deepcopy(value.get('common',{})),**dict(zip(value['columns'],deepcopy(row)))}
            for key in value.get('absent',{}).get(str(i),[]):record.pop(key)
            result.append(record)
        return result
    return deepcopy(value)


TABLE_INSTRUCTION = ('record_table_v1 is lossless: each row is one object, pairing values with columns, '
                     'then adding common fields; absent[str(row_index)] lists keys to omit (not null). '
                     'Every definition, condition and evidence value is retained.')


def intern_strings(value):
    counts=Counter();prefix_counts=Counter()
    def scan(x):
        if isinstance(x,dict):
            for v in x.values():scan(v)
        elif isinstance(x,list):
            for v in x:scan(v)
        elif isinstance(x,str):
            counts[x]+=1
            match=re.match(r'<?urn:(?:[^:]+:){2}(?:class|property):',x)
            if match:prefix_counts[match[0]]+=1
    scan(value)
    strings=[s for s,n in counts.items() if n>1 and len(s)>16]
    prefixes=[s for s,n in prefix_counts.items() if n>1]
    ids={s:'@s'+str(i) for i,s in enumerate(strings)}
    def encode(x):
        if isinstance(x,dict):return {k:encode(v) for k,v in x.items()}
        if isinstance(x,list):return [encode(v) for v in x]
        if not isinstance(x,str):return x
        if x in ids:return ids[x]
        for i,prefix in enumerate(prefixes):
            if x.startswith(prefix):return '@p'+str(i)+':'+x[len(prefix):]
        return '@literal:'+x if x.startswith('@') else x
    return {'encoding':'string_pool_v1','strings':strings,'prefixes':prefixes,'data':encode(value)}


def expand_strings(value):
    def decode(x):
        if isinstance(x,dict):return {k:decode(v) for k,v in x.items()}
        if isinstance(x,list):return [decode(v) for v in x]
        if not isinstance(x,str):return x
        if x.startswith('@literal:'):return x[len('@literal:'):]
        if re.fullmatch(r'@s\d+',x):return value['strings'][int(x[2:])]
        if re.match(r'@p\d+:',x):
            head,tail=x.split(':',1);return value['prefixes'][int(head[2:])]+tail
        return x
    return decode(value['data'])


def compact_ontology(payload):
    context = payload.get('ontology_context')
    if context and len(json.dumps(context, ensure_ascii=False).encode('utf-8')) > 8000:
        for key in ('properties', 'classes'):
            if isinstance(context.get(key), list):
                context[key] = record_table(context[key])
        payload['ontology_context'] = intern_strings(context)
        payload['context_encoding'] = TABLE_INSTRUCTION + (' string_pool_v1: decode @sN as strings[N], '
            '@pN:suffix as prefixes[N]+suffix, @literal:text as text. Decode before interpreting IDs; return full original IDs.')
        # These hashes are verified in the durable plan before/after the call;
        # they are repeated packaging, not evidence or semantic constraints.
        for key in ('original_response_hash','proposal_digest','evidence_hash','ontology_context_hash','output_template'):
            payload.pop(key,None)
        for error in payload.get('errors',[]):
            # The identical field schema remains in output_contract.repairs.
            error.pop('expected_schema',None)
    return payload
