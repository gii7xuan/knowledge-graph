"""Canonical, intersectable permissions for historical labeled examples."""
from copy import deepcopy
from .contracts import sha256_json

VERSION = 'review_memory_scope_v1'
DIMENSIONS = ('document_ids', 'run_ids', 'candidate_ids')


def normalize_scope(value=None):
    value = deepcopy(value if value is not None else {'mode': 'all_published'})
    if not isinstance(value, dict) or set(value)-{'version','mode',*(p+d for p in ('allowed_','excluded_') for d in DIMENSIONS)}:
        raise ValueError('invalid memory scope fields')
    if value.get('version', VERSION) != VERSION or value.get('mode') not in {'all_published','restricted','disabled'}:
        raise ValueError('invalid memory scope version/mode')
    result = {'version': VERSION, 'mode': value['mode']}
    for name in (p+d for p in ('allowed_','excluded_') for d in DIMENSIONS):
        if name not in value: continue
        rows = value[name]
        if not isinstance(rows,list) or any(not isinstance(x,str) or not x.strip() for x in rows):
            raise ValueError('memory scope IDs must be nonempty strings')
        result[name] = sorted(set(rows))
    if result['mode']=='restricted' and not any('allowed_'+d in result for d in DIMENSIONS):
        raise ValueError('restricted memory requires an explicit allowlist (empty denies all)')
    if result['mode']=='all_published' and any('allowed_'+d in result for d in DIMENSIONS):
        raise ValueError('allowlists require restricted memory mode')
    return result


def intersect_scopes(left, right):
    left,right=normalize_scope(left),normalize_scope(right)
    if 'disabled' in (left['mode'],right['mode']): return normalize_scope({'mode':'disabled'})
    result={'mode':'restricted' if 'restricted' in (left['mode'],right['mode']) else 'all_published'}
    for dimension in DIMENSIONS:
        key='allowed_'+dimension
        present=[set(s[key]) for s in (left,right) if key in s]
        if present: result[key]=sorted(set.intersection(*present))
        excluded=set(left.get('excluded_'+dimension,[]))|set(right.get('excluded_'+dimension,[]))
        if excluded: result['excluded_'+dimension]=sorted(excluded)
    return normalize_scope(result)


def resolve_scope(config, context=None, *, pinned=None):
    context=context or {}
    if (config.get('memory') or {}).get('enabled') is False: return normalize_scope({'mode':'disabled'})
    configured=normalize_scope((config.get('memory') or {}).get('scope'))
    requested=context.get('memory_scope',pinned)
    return intersect_scopes(configured,normalize_scope(requested)) if requested is not None else configured


def scope_allows(scope,candidate):
    if scope['mode']=='disabled': return False
    for dimension,field in zip(DIMENSIONS,('document_id','run_id','candidate_id')):
        value=candidate.get(field)
        if 'allowed_'+dimension in scope and value not in scope['allowed_'+dimension]: return False
        if value in scope.get('excluded_'+dimension,[]): return False
    return True


def scope_from_split(split):
    if not isinstance(split,dict): raise ValueError('split must be an object')
    sets=[]
    for key in ('train_document_ids','calibration_document_ids','test_document_ids'):
        values=split.get(key,[])
        if not isinstance(values,list) or any(not isinstance(x,str) or not x for x in values): raise ValueError('invalid split document IDs')
        sets.append(set(values))
    train,calibration,test=sets
    if train&calibration or train&test or calibration&test: raise ValueError('document partitions overlap')
    return normalize_scope({'mode':'restricted','allowed_document_ids':sorted(train),
        'excluded_document_ids':sorted(calibration|test)})


def scope_hash(scope): return sha256_json(normalize_scope(scope))
