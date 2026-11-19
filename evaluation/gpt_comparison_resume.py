"""Continue untouched candidates while quarantining every unknown-outcome case."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from .freeze_shared_ontology import ROOT,read,write
from review_agent.contracts import sha256_json


def preparation(prior,inputs):
    from .gpt_five_group import GROUPS
    if not prior.is_relative_to(ROOT/'outputs') or not (prior/'finished.json').exists():
        raise ValueError('resume requires a finished local study')
    old=read(prior/'model_inputs.json')
    if [(x['sample_id'],x['reference_binding']) for x in inputs]!=[(x['sample_id'],x['reference_binding']) for x in old]:
        raise ValueError('resume population or GPT packet changed')
    verification=read(prior/'final_verification.json')
    paths=verification['database_sha256'];unique={}
    for name,digest in paths.items():
        path=Path(name).resolve()
        if not path.is_relative_to(ROOT/'outputs') or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('original request database changed')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as connection:
            for attempt,state,raw,metadata in connection.execute('SELECT attempt_id,state,result_json,metadata_json FROM request_attempts'):
                if state=='running':raise ValueError('wait for existing client workers before resuming')
                m=json.loads(metadata);p=m.get('proposal') or {};r=json.loads(raw or '{}')
                unique[attempt]={'state':state,'candidate_id':p.get('candidate_id') or p.get('candidate',{}).get('candidate_id'),
                    'input_tokens':r.get('input_tokens'),'output_tokens':r.get('output_tokens'),'not_sent':r.get('not_sent')}
    unknown={r['candidate_id'] for r in unique.values() if r['state']=='unknown'}
    if None in unknown:raise ValueError('unknown request cannot be associated with a candidate')
    predictions={g:read(prior/(g+'_predictions.json')) for g in GROUPS}
    for rows in predictions.values():
        if len(rows)!=len(inputs) or any(r['sample_id']!=s['sample_id'] or r['binding']!=s['reference_binding'] for r,s in zip(rows,inputs)):
            raise ValueError('prior prediction packet binding changed')
    done=[s['sample_id'] for s,a,b in zip(inputs,predictions['b3_fixed_workflow'],predictions['agent'])
        if a['status'] not in {'not_started','skipped_unknown'} and b['status'] not in {'not_started','skipped_unknown'} and s['candidate']['candidate_id'] not in unknown]
    artifacts=[g+'_predictions.json' for g in GROUPS]+['model_inputs.json','protocol.json','public_settings.json',
        'gpt_reference.json','initial_model_reference_comparisons.json','weight_replay.json']
    physical=[r for r in unique.values() if not r['not_sent']]
    known=sum((r['input_tokens'] or 0)+(r['output_tokens'] or 0) for r in physical)
    if len(physical)!=verification['unique_provider_requests'] or known!=verification['known_tokens']:
        raise ValueError('prior physical request accounting changed')
    source_protocol=read(prior/'protocol.json')
    return {'version':'skip_unknown_resume_v1','prior_directory':str(prior),
        'database_sha256':paths,'artifact_sha256':{str(prior/n):hashlib.sha256((prior/n).read_bytes()).hexdigest() for n in artifacts},
        'prior_unique_requests':len(physical),'prior_known_tokens':known,
        'prior_unknown_attempt_ids':[k for k,r in unique.items() if r['state']=='unknown'],
        'prior_started_at':source_protocol.get('continuation',{}).get('prior_started_at',read(prior/'started.json')['time']),
        'prior_settings_hash':sha256_json(read(prior/'public_settings.json')),
        'completed_sample_ids':done,'skipped_candidate_ids':sorted(unknown),
        'remaining_sample_ids':[s['sample_id'] for s in inputs if s['sample_id'] not in done and s['candidate']['candidate_id'] not in unknown],
        'user_authorization':'skip unknown-outcome candidates and continue the remaining candidates; no unknown request is resent',
        'review_configuration_changed':False,'prior_completed_predictions_reused_without_changes':True,
        'unknown_policy':'quarantine an entire candidate across all arms; continue other candidates within the original total budget'}


def restore(output,record,inputs,fence):
    from .gpt_five_group import GROUPS
    from review_agent.request_identity import semantic_request_key
    for name,digest in {**record['database_sha256'],**record['artifact_sha256']}.items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest:raise ValueError('frozen resume source changed')
    if sha256_json(read(output/'public_settings.json'))!=record['prior_settings_hash']:
        raise ValueError('review model/settings changed during resume')
    fence.skip_unknown_candidates=True
    fence.quarantined_candidates.update(record['skipped_candidate_ids'])
    for name in record['database_sha256']:
        path=Path(name);fence.register(path)
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
            for raw in connection.execute('SELECT metadata_json FROM request_attempts'):
                m=json.loads(raw[0])
                if m.get('task_type')=='planner_action':
                    p=m['proposal'];fence.prior_semantics.add(semantic_request_key('planner_action',m['binding'],p,p.get('prompt_hash')))
    fence.reservations=record['prior_unique_requests'];fence.prior_requests=record['prior_unique_requests']
    fence.started=time.monotonic()-max(0,time.time()-record['prior_started_at'])
    snapshot=fence.snapshot()
    if snapshot['unique_provider_attempts']!=record['prior_unique_requests'] or snapshot['known_tokens']!=record['prior_known_tokens']:
        raise ValueError('resume seed accounting mismatch')
    prior=Path(record['prior_directory']);predictions={g:read(prior/(g+'_predictions.json')) for g in GROUPS}
    comparisons=read(prior/'initial_model_reference_comparisons.json')
    replay=read(prior/'weight_replay.json')
    for ordinal,sample in enumerate(inputs):
        if sample['candidate']['candidate_id'] in fence.quarantined_candidates:
            quarantine(output,ordinal,sample,predictions,fence)
    write(output/'resume_receipt.json',{'prior_provider_requests':record['prior_unique_requests'],
        'prior_known_tokens':record['prior_known_tokens'],'completed_rows_reused':len(record['completed_sample_ids']),
        'remaining_candidates':len(record['remaining_sample_ids']),'skipped_candidate_ids':record['skipped_candidate_ids'],
        'new_requests_at_restore':0,'prior_unknown_requests_resent':0})
    return predictions,comparisons,replay,set(record['completed_sample_ids'])


def quarantine(output,ordinal,sample,predictions,fence):
    """Historical name: preserve valid arm results and route failed arms to humans."""
    from .gpt_five_group import GROUPS,frame
    from .human_handoff import normalize,technical_failure
    candidate_id=sample['candidate']['candidate_id']
    attempts=[r['attempt_id'] for r in fence.records.values() if r.get('candidate_id')==candidate_id and r['state'] in {'running','unknown'}]
    path=output/('human_handoff_case_'+str(ordinal+1).zfill(2)+'.json')
    before={g:deepcopy(predictions[g][ordinal]) for g in GROUPS}
    for group in GROUPS:
        row=before[group]
        if row.get('status')=='skipped_unknown' and row.get('partial_results_archive'):
            archive=read(Path(row['partial_results_archive']))
            if archive['candidate_id']!=candidate_id:raise ValueError('handoff archive candidate mismatch')
            row=archive['partial_results_excluded_from_comparison'][group]
        if group=='b0_all_keep':
            row=frame(sample,'completed','accept',decision_is_official_admission=False)
        blocked_initial=row.get('status')=='not_started'
        failed=technical_failure(row) or blocked_initial
        predictions[group][ordinal]=normalize(row,
            reason='initial_committee_unavailable' if blocked_initial else
                'timeout_or_request_outcome_unknown' if failed and attempts else None,
            technical=failed)
        predictions[group][ordinal]['unknown_requests_resent']=False
        if predictions[group][ordinal]['human_review_required']:
            predictions[group][ordinal]['handoff_audit_ref']=str(path)
    if not path.exists():
        write(path,{'sample_id':sample['sample_id'],'candidate_id':candidate_id,'reason':'request_outcome_unknown',
            'unknown_attempt_ids':attempts,'automatic_resend':False,
            'original_method_results':before,
            'operator_outcomes':{g:predictions[g][ordinal]['final_outcome'] for g in GROUPS}})
    (output/'manual_review_routing.json').write_text(json.dumps({'planned_population':40,
        'unknown_result_candidate_ids':sorted(fence.quarantined_candidates),'automatic_resend':False,
        'evaluation_population':40,'excluded_candidates':0},ensure_ascii=False,indent=2),encoding='utf-8')
