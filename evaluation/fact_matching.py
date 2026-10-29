"""Exact one-to-one semantic matching; display text never grants identity."""
import re
from review_agent.contracts import fact_key_for,normalized_qualifiers

VERSIONS={'fact_identity_v1','fact_identity_v2'}
ACCEPTED={'auto_accept','human_accept','accept'}


def semantic_key(row):
    candidate=row.get('final_candidate') or row.get('candidate') or row
    if not isinstance(candidate,dict): return None
    if candidate.get('fact_key_state')!='canonical': return None
    if candidate.get('fact_identity_version') not in VERSIONS: return None
    declared=candidate.get('fact_key')
    if not isinstance(declared,str) or not re.fullmatch('[0-9a-f]{64}',declared): return None
    if isinstance(candidate.get('triple'),dict):
        triple=candidate['triple']; qualifiers=normalized_qualifiers(triple.get('qualifiers'))
        if (candidate.get('contract_version')!='extraction_candidate_v4' or not triple.get('head_entity_id')
                or not triple.get('relation_id') or qualifiers['polarity'] not in {'positive','negative'}
                or not qualifiers['modality'] or any(v=='unknown' for v in (triple.get('qualifier_status') or {}).values())): return None
        if triple.get('tail_kind')=='entity' and not triple.get('tail_entity_id'): return None
        if candidate['fact_identity_version']=='fact_identity_v2' and not triple.get('relation_semantic_hash'): return None
        if fact_key_for(candidate)!=declared: raise ValueError('semantic fact key differs from the bound candidate')
    # Compact records are accepted only with an explicit canonical state and
    # identity version; frozen manifests bind these to the source candidate.
    return candidate['fact_identity_version']+':'+declared


def identity_summary(rows):
    keys=[semantic_key(row) for row in rows]
    known=[k for k in keys if k is not None]
    return {'source_row_count':len(rows),'canonical_identity_count':len(known),
        'unknown_identity_count':len(rows)-len(known),'distinct_fact_count':len(set(known)),
        'duplicate_rate':1-len(set(known))/len(known) if known else None,
        'duplicate_denominator':'canonical_identity_rows'}


def match_facts(predictions,gold):
    gold_keys=[semantic_key(row) for row in gold]
    if any(k is None for k in gold_keys): raise ValueError('gold facts need explicit canonical semantic identities')
    gold_set=set(gold_keys)
    accepted=[row for row in predictions if row.get('decision') in ACCEPTED]
    summary=identity_summary(accepted)
    predicted={k for row in accepted if (k:=semantic_key(row)) is not None}
    matched=predicted&gold_set
    return {**summary,'gold_fact_count':len(gold_set),'matched_fact_count':len(matched),
        'false_positive_fact_count':len(predicted-gold_set),'missing_gold_fact_count':len(gold_set-predicted),
        'fact_precision':len(matched)/len(predicted) if predicted and not summary['unknown_identity_count'] else None,
        'fact_recall':len(matched)/len(gold_set) if gold_set else None,'matching_policy':'exact_semantic_identity_one_to_one'}
