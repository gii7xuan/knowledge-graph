"""Keep revisions, duplicate facts and substantially overlapping spans together."""
from difflib import SequenceMatcher
from review_agent.contracts import canonical_json,sha256_json


def leakage_groups(candidates):
    parents=list(range(len(candidates)))
    def root(i):
        while parents[i]!=i: parents[i]=parents[parents[i]];i=parents[i]
        return i
    for i,a in enumerate(candidates):
        for j,b in enumerate(candidates[:i]):
            same_fact=(a.get('fact_key_state')=='canonical' and b.get('fact_key_state')=='canonical' and bool(a.get('fact_key')) and a.get('fact_key')==b.get('fact_key'))
            shared=(a.get('document_id'),a.get('document_version'))==(b.get('document_id'),b.get('document_version'))
            spans_a=[(x['start'],x['end']) for x in a.get('evidence_items',[]) if x.get('offset_basis')=='canonical_document']
            spans_b=[(x['start'],x['end']) for x in b.get('evidence_items',[]) if x.get('offset_basis')=='canonical_document']
            overlap=shared and any(max(0,min(y,v)-max(x,u))>=.5*min(y-x,v-u) for x,y in spans_a for u,v in spans_b)
            text=lambda c:'|'.join(str(c.get('triple',{}).get(k) or '').strip().casefold() for k in ('head','surface_relation','tail'))
            complete=all(a.get('triple',{}).get(k) and b.get('triple',{}).get(k) for k in ('head','surface_relation','tail'))
            duplicate=bool(complete and a['triple']['head']==b['triple']['head'] and
                a['triple']['surface_relation']==b['triple']['surface_relation'] and SequenceMatcher(None,text(a),text(b)).ratio()>=.9)
            if a['candidate_id']==b['candidate_id'] or same_fact or overlap or duplicate: parents[root(i)]=root(j)
    groups={}
    for i,c in enumerate(candidates):groups.setdefault(root(i),[]).append((c['candidate_id'],c['revision']))
    return [sorted(group) for group in groups.values()]


def verify_split(candidates,assignments):
    groups=leakage_groups(candidates)
    for group in groups:
        roles={assignments[item] for item in group}
        if not roles<={'development','independent_test','training','calibration'} or len(roles)!=1:
            raise ValueError('related facts/revisions/overlapping evidence cross evaluation splits')
    return {'split_integrity_version':'source_fact_split_v1','groups':groups,'groups_hash':sha256_json(groups),
            'development_and_independent_test_reported_separately':True}
