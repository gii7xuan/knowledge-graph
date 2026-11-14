"""Keep source documents, revisions and duplicate facts in one partition."""
from difflib import SequenceMatcher
from review_agent.contracts import sha256_json


def leakage_groups(candidates):
    parents=list(range(len(candidates)))
    def root(i):
        while parents[i]!=i: parents[i]=parents[parents[i]];i=parents[i]
        return i
    for i,a in enumerate(candidates):
        for j,b in enumerate(candidates[:i]):
            same_fact=(a.get('fact_key_state')=='canonical' and b.get('fact_key_state')=='canonical' and bool(a.get('fact_key')) and a.get('fact_key')==b.get('fact_key'))
            # A new revision or a disjoint excerpt of a development document is
            # still the same source, not an independent test document.
            shared=bool(a.get('document_id')) and a.get('document_id')==b.get('document_id')
            # document_version is the canonical document text hash, so renaming
            # a source document cannot make identical source text independent.
            same_text=bool(a.get('document_version')) and a.get('document_version')==b.get('document_version')
            text=lambda c:'|'.join(str(c.get('triple',{}).get(k) or '').strip().casefold() for k in ('head','surface_relation','tail'))
            complete=all(a.get('triple',{}).get(k) and b.get('triple',{}).get(k) for k in ('head','surface_relation','tail'))
            normalized=lambda c,k:str(c['triple'][k]).strip().casefold()
            duplicate=bool(complete and normalized(a,'head')==normalized(b,'head') and
                normalized(a,'surface_relation')==normalized(b,'surface_relation') and SequenceMatcher(None,text(a),text(b)).ratio()>=.9)
            if a['candidate_id']==b['candidate_id'] or same_fact or shared or same_text or duplicate: parents[root(i)]=root(j)
    groups={}
    for i,c in enumerate(candidates):groups.setdefault(root(i),[]).append((c['candidate_id'],c['revision']))
    return [sorted(group) for group in groups.values()]


def verify_split(candidates,assignments):
    if any(assignments[(c['candidate_id'],c['revision'])]=='independent_test' and
           any(not isinstance(c.get(k),str) or not c[k].strip() for k in ('document_id','document_version'))
           for c in candidates):
        raise ValueError('independent tests require a bound source document and version')
    groups=leakage_groups(candidates)
    for group in groups:
        roles={assignments[item] for item in group}
        if not roles<={'development','independent_test','training','calibration'} or len(roles)!=1:
            raise ValueError('related facts/revisions/source documents/evidence cross evaluation splits')
    return {'split_integrity_version':'source_fact_split_v2','groups':groups,'groups_hash':sha256_json(groups),
            'development_and_independent_test_reported_separately':True}
