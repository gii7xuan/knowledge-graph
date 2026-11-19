"""Reuse original settled ballots; preserve the cost of an interrupted prefix."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from review_agent.contracts import sha256_json
from .freeze_shared_ontology import ROOT,read,write


def continuation(prior,inputs):
    if not prior.is_relative_to(ROOT/'outputs'):raise ValueError('prior study must be under outputs')
    receipt=read(prior/'interruption_receipt.json')
    if receipt['unknown_requests'] or (prior/'finished.json').exists():raise ValueError('only a settled interrupted prefix can be continued')
    old=read(prior/'model_inputs.json')
    if [(s['sample_id'],s['reference_binding']) for s in inputs]!=[(s['sample_id'],s['reference_binding']) for s in old]:
        raise ValueError('continuation population or reference bindings changed')
    paths={}
    for relative,digest in receipt['database_sha256'].items():
        path=prior/relative
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('interrupted database changed')
        paths[str(path)]=digest
    commons={}
    for ordinal,sample in enumerate(inputs,1):
        path=prior/('case_'+str(ordinal).zfill(2))/'common.sqlite3'
        if str(path) in paths:commons[sample['sample_id']]=str(path)
    return {'prior_directory':str(prior),'database_sha256':paths,'common_databases':commons,
        'prior_unique_requests':receipt['unique_provider_requests'],'prior_known_tokens':receipt['known_tokens'],
        'prior_started_at':read(prior/'started.json')['time'],
        'excluded_results':'old B3/Agent branch outcomes are engineering diagnostics; their real cost remains charged',
        'reused_initial_ballots':len(commons),'initial_requests_repeated':0}


def seed(fence,record):
    from review_agent.request_identity import semantic_request_key
    for name,digest in record['database_sha256'].items():
        path=Path(name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('prior database changed since freeze')
        fence.register(path)
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
            for raw in connection.execute('SELECT metadata_json FROM request_attempts'):
                m=json.loads(raw[0])
                if m.get('task_type')=='planner_action':
                    p=m['proposal'];v=m['binding']
                    fence.prior_semantics.add(semantic_request_key('planner_action',v,p,p.get('prompt_hash')))
    fence.reservations=record['prior_unique_requests'];fence.prior_requests=record['prior_unique_requests']
    fence.started=time.monotonic()-max(0,time.time()-record['prior_started_at'])
    snapshot=fence.snapshot()
    if snapshot['unique_provider_attempts']!=record['prior_unique_requests'] or snapshot['unknown_requests']:
        raise ValueError('interrupted request accounting changed')


def restore_initial(runtime,candidate,index):
    """Hydrate late receipts without executing an LLM task or rebinding a vote."""
    from review_agent.validator_contracts import validate_vote,proposal_errors
    repository=runtime.repository
    with repository.connect() as connection:
        row=connection.execute('SELECT audit_id FROM agent_checkpoints').fetchall()
    if len(row)!=1:raise ValueError('exactly one original initial checkpoint required')
    runtime.initialize(candidate['candidate_id'],candidate['revision'],index,{'audit_id':row[0][0]})
    audit=repository.get_audit(candidate['candidate_id'],runtime.state.audit_id)
    if len(audit['rounds'])!=1:raise ValueError('continuation requires one original initial proposal')
    proposal=audit['rounds'][0]
    with repository.connect() as connection:
        rows=connection.execute("SELECT state,result_json FROM request_attempts WHERE json_extract(metadata_json,'$.task_type')='triple_review'").fetchall()
    votes=[json.loads(r[1]) for r in rows]
    expected={v['id'] for v in runtime.orchestrator.validators}
    if len(rows)!=3 or any(r[0]!='completed' for r in rows) or {v['voter_id'] for v in votes}!=expected:
        raise ValueError('all original initial results must be settled and present')
    for vote in votes:
        if vote['audit_id']!=runtime.state.audit_id or vote['proposal_digest']!=proposal['proposal_digest']:
            raise ValueError('cannot rebind an original ballot')
        if validate_vote(vote,proposal['response_contract_version']) or proposal_errors(vote,proposal):
            raise ValueError('original ballot no longer validates')
    # Keep already persisted votes byte-for-byte. Add only late, missing receipts.
    existing={v['voter_id']:v for v in audit['votes']}
    missing=[v for v in votes if v['voter_id'] not in existing]
    if missing:repository.append_round(runtime.state.audit_id,proposal,missing,{}, {'recovered_original_receipts':True})
    ballots=[existing.get(v['voter_id'],v) for v in votes]
    state=runtime.state
    state.context['agent_resolution_policy']={'max_attempts_per_issue':3,'max_semantic_revisions':0,
        'reserve_final_committee_calls':0,'fixed_candidate_limits':True}
    state.context['continuation_initial_receipts']=[v['attempt_id'] for v in votes]
    state.context['resolution_no_further_validation']=True
    runtime.budget.usage.update(llm_calls=3,validation_rounds=1,
        input_tokens=sum(v['input_tokens'] for v in votes),output_tokens=sum(v['output_tokens'] for v in votes))
    state.current_validation_key=state.validation_key()
    runtime.tools.accept_validation(proposal,ballots,state.current_validation_key)
    state.pending_action={};runtime.checkpoint()


def branch_window(runtime,folder):
    """Charge actual initial-review time; exclude only administrative idle time."""
    with runtime.repository.connect() as connection:
        received=connection.execute("SELECT MAX(response_received_at) FROM request_attempts WHERE json_extract(metadata_json,'$.task_type')='triple_review'").fetchone()[0]
    if received is None:raise ValueError('initial completion timestamp required')
    original=runtime.budget.deadline_epoch
    remaining=max(0,min(float(runtime.budget.config['total_timeout_seconds']),original-received))
    shifted=time.time()+remaining
    runtime.budget.deadline_epoch=shifted;runtime.budget.started=time.monotonic();runtime.budget.allowed_seconds=remaining
    runtime.state.context['execution_deadline_epoch']=shifted
    write(folder/'branch_clock.json',{'original_initial_deadline':original,'initial_results_received_at':received,
        'shared_branch_deadline':shifted,'remaining_active_seconds':remaining,
        'administrative_pause_excluded':True,'applies_equally_to':['b3_fixed_workflow','agent']})
    runtime.checkpoint()


def projection(repository,candidate,result,proposal_digest):
    """Report bound axis observations even when conservative admission stays pending."""
    from review_agent.resolution_state import apply_consensus
    audit=repository.get_audit(candidate['candidate_id'],result['audit_id'])
    context=audit['context'];snapshot=context.get('resolution_snapshot')
    axes=deepcopy(result.get('review_axes') or {});role='final_review_axes';evidence=None
    if snapshot:
        if snapshot['candidate_hash']!=sha256_json(candidate) or snapshot['base_proposal_digest']!=proposal_digest:
            raise ValueError('post-review diagnostic snapshot binding mismatch')
        axes=apply_consensus({'review_axes':axes},snapshot)['review_axes']
        role='bound_resolution_axis_observations_not_admission_truth';evidence=snapshot['evidence_hash']
    failures=[]
    with repository.connect() as connection:
        for attempt,state,raw in connection.execute('SELECT attempt_id,state,result_json FROM request_attempts'):
            r=json.loads(raw or '{}')
            if state in {'running','unknown'} or r.get('response_status')!='ok':
                failures.append({'attempt_id':attempt,'state':state,'response_status':r.get('response_status'),'error_code':r.get('error_code')})
        for step,status,raw in connection.execute("SELECT step_no,status,record_json FROM agent_tool_calls WHERE audit_id=? AND status IN ('invalid_action','tool_error')",(result['audit_id'],)):
            detail=json.loads(raw)
            failures.append({'tool_step':step,'state':status,'error':detail.get('error')})
    for step in audit.get('planner_steps',[]):
        for receipt in step.get('attempts',[]):
            if receipt.get('not_sent') and receipt.get('response_status')!='ok':
                failures.append({'planner_step':step['step_no'],'state':'not_sent','error_code':receipt.get('error_code')})
    # Valid but uncertain/contradictory/unassigned judgments are domain outcomes.
    status=result.get('status') or ('technical_incomplete' if failures else 'completed')
    return {'axes':axes,'role':role,'evidence_hash':evidence,'technical_failures':failures,'status':status}
