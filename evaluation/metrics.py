"""Research metrics with explicit human denominators and unknown accounting."""
from collections import Counter
from .fact_matching import identity_summary,match_facts,ACCEPTED
from .manifest import key


def ratio(a,b): return a/b if b else None
def binary(value): return type(value) is int and value in (0,1)


def current_truth(rows,revision_labels=None):
    labels={}
    for row in revision_labels or []:
        identity=key(row)
        if identity in labels: raise ValueError('duplicate revision truth')
        labels[identity]=row
    used=set(); result=[]
    for source in rows:
        row=dict(source); final=row.get('final_candidate') or {}
        revision=final.get('revision',row.get('revision',1))
        row['original_label']=row.get('label')
        if revision!=row.get('revision',1):
            identity=(row['candidate_id'],revision); truth=labels.get(identity,{})
            if truth:
                from review_agent.contracts import sha256_json
                if truth.get('candidate_content_hash')!=sha256_json(final): raise ValueError('new revision truth must bind the exact final candidate')
                used.add(identity)
            for field in ('label','valid_for_kg','source_support','schema_valid','task_relevance'):
                row[field]=truth.get(field)
            if row.get('label') is None: row['label']=row.get('valid_for_kg')
            if row.get('label') is not None and not binary(row['label']): raise ValueError('invalid new-revision gold label')
            row['truth_revision']=revision; row['revision_truth_missing']=not bool(truth)
        result.append(row)
    if set(labels)-used: raise ValueError('revision labels do not reference an evaluated final revision')
    return result


def research_metrics(rows,*,gold_facts=None,extension_records=None,extension_labels=None):
    # Predicted accepts remain recommendations, distinct from real admission.
    predicted=lambda r:r.get('predicted_decision')=='accept' or r.get('decision') in ACCEPTED
    accepted=[r for r in rows if predicted(r)]
    judged=[r for r in accepted if binary(r.get('label'))]
    positives=[r for r in rows if r.get('label')==1 and binary(r.get('label'))]
    rejected=[r for r in rows if r.get('predicted_decision')=='reject' or r.get('decision') in {'auto_reject','human_reject'}]
    partial={field:sum(r.get(field) is not None for r in rows) for field in ('source_support','schema_valid','task_relevance','valid_for_kg')}
    evidence=[a for r in rows for a in r.get('evidence_anchors',[])]
    located=[a for a in evidence if type(a.get('anchor_valid')) is bool]
    semantic=[]
    for row in rows:
        available={a['evidence_id'] for a in row.get('evidence_anchors',[]) if a.get('evidence_id')}
        seen=set()
        for annotation in row.get('evidence_labels',[]):
            eid=annotation.get('evidence_id')
            if eid not in available or eid in seen: raise ValueError('semantic evidence truth must reference a unique current anchor')
            seen.add(eid)
            if binary(annotation.get('semantic_valid')): semantic.append(annotation)
    issues=[i for r in rows for i in r.get('issue_history',[])]
    conflicts=[i for i in issues if i.get('code') in {'source_conflict','counterevidence'}]
    revisions=[r for r in rows if (r.get('final_candidate') or {}).get('revision',r.get('revision',1))!=r.get('revision',1)]
    gaps=[r for r in rows if r.get('ontology_gap')==1 and binary(r.get('ontology_gap'))]
    result={'recommendation_precision':ratio(sum(r['label'] for r in judged),len(judged)),
        'recommendation_labeled_accept_count':len(judged),'recommendation_unknown_accept_count':len(accepted)-len(judged),
        'recommendation_coverage':ratio(len(accepted),len(rows)),
        'false_rejection_rate':ratio(sum(r in rejected for r in positives),len(positives)),
        'dimension_annotation_counts':partial,'anchor_valid_rate':ratio(sum(a['anchor_valid'] for a in located),len(located)),
        'anchor_validation_count':len(located),'anchor_validation_unknown_count':len(evidence)-len(located),
        'evidence_semantic_valid_rate':ratio(sum(a['semantic_valid'] for a in semantic),len(semantic)),
        'evidence_semantic_labeled_count':len(semantic),
        'evidence_semantic_unknown_count':max(0,len(evidence)-len(semantic)),
        'conflict_resolution_rate':ratio(sum(i.get('status')=='resolved' for i in conflicts),len(conflicts)),
        'conflict_count':len(conflicts),'conflict_resolution_is_system_behavior_not_gold_accuracy':True,
        'fallback_count':sum('fallback' in str(r.get('executed_strategy','')) or r.get('executed_strategy')=='manual_degraded' for r in rows),
        'budget_exhausted_count':sum('budget' in str(r.get('stop_reason','')) for r in rows),
        'projection_status_counts':dict(Counter(r.get('projection_status','unreported') for r in rows)),
        'projection_error_rate':ratio(sum(r.get('projection_status')=='failed' for r in rows),sum(r.get('projection_status') in {'completed','failed'} for r in rows)),
        'revision_count':len(revisions),'revision_labeled_count':sum(binary(r.get('label')) for r in revisions),
        'revision_known_correct_accept_count':sum(r.get('label')==1 and predicted(r) for r in revisions),
        'revision_success_rate_labeled':ratio(sum(r.get('label')==1 and predicted(r) for r in revisions),sum(binary(r.get('label')) for r in revisions)),
        'independently_labeled_gap_count':len(gaps),'gap_final_truth_unknown_count':sum(not binary(r.get('label')) for r in gaps),
        'gap_known_correct_acceptance_rate':ratio(sum(r.get('label')==1 and predicted(r) for r in gaps),len(gaps)),
        'gap_rate_interpretation':'known-correct lower bound when final truth is incomplete',
        'source_level_known_correct_accept_count':sum(r.get('label')==1 for r in accepted),
        'accepted_fact_identity':identity_summary([r.get('final_candidate') or r for r in accepted])}
    if gold_facts is not None:
        result['fact_matching']=match_facts([{**r,'decision':'accept' if predicted(r) else 'manual_review'} for r in rows],gold_facts)
    records=extension_records or []; annotations={}
    for label in extension_labels or []:
        k=(label['extension_id'],label['delta_revision'],label['proposal_digest'])
        if k in annotations: raise ValueError('duplicate ontology proposal gold label')
        annotations[k]=label
    seen=set(); signatures=[]; approved=[]; known=[]
    for record in records:
        k=(record['extension_id'],record['delta_revision'],record.get('proposal_digest'))
        seen.add(k)
        if record.get('modeling_signature'): signatures.append(record['modeling_signature'])
        if record.get('recommendation')=='approve':
            approved.append(record)
            if binary(annotations.get(k,{}).get('correct')): known.append(annotations[k])
    if set(annotations)-seen: raise ValueError('extension truth does not bind an observed proposal')
    result['extensions']={'proposal_count':len(records),'approved_count':len(approved),'approval_labeled_count':len(known),
        'approval_precision':ratio(sum(r['correct'] for r in known),len(known)),
        'exact_duplicate_proposal_rate':1-len(set(signatures))/len(signatures) if signatures else None,
        'duplicate_policy':'exact base and normalized modeling operations, excluding evidence IDs',
        'published_count':sum(bool(r.get('published_release_id')) for r in records),
        'affected_historical_candidate_count':len({x for r in records for x in r.get('affected_candidate_ids',[])})}
    return result


def usage_metrics(requests,prices=None):
    prices=prices or {}; seen=set(); grouped={}; totals=[]
    for source in requests:
        record=dict(source); identity=record['request_id']
        if identity in seen: raise ValueError('duplicate request usage record')
        seen.add(identity); totals.append(record)
        grouped.setdefault(record.get('role','unknown'),[]).append(record)
    def summarize(records):
        known_cost=0.; unknown_cost=0
        for r in records:
            price=prices.get(r.get('model_id'),{})
            if (all(type(r.get(k)) is int and r[k]>=0 for k in ('input_tokens','output_tokens'))
                    and all(type(price.get(k)) in (int,float) and price[k]>=0 for k in ('input_per_million','output_per_million'))):
                known_cost+=(r['input_tokens']*price['input_per_million']+r['output_tokens']*price['output_per_million'])/1_000_000
            else: unknown_cost+=1
        return {'calls':len(records),'retries':sum(bool(r.get('retry')) for r in records),
            'known_input_tokens':sum(r.get('input_tokens') or 0 for r in records),
            'known_output_tokens':sum(r.get('output_tokens') or 0 for r in records),
            'unknown_token_requests':sum(any(r.get(k) is None for k in ('input_tokens','output_tokens')) for r in records),
            'known_latency_ms':sum(r.get('latency_ms') or 0 for r in records),
            'outcome_unknown_requests':sum(r.get('status') in {'started','outcome_unknown','timeout'} for r in records),
            'known_cost':known_cost,'cost':known_cost if not unknown_cost else None,'unknown_cost_requests':unknown_cost}
    return {'total':summarize(totals),'by_role':{role:summarize(items) for role,items in grouped.items()},
        'shared_request_accounting':'one physical request counted once; case charges remain in SQLite',
        'price_source':'explicit user-supplied rates','currency':'user_configured'}
