"""One paid action-format check of the compact view; never execute its action."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile,ZIP_DEFLATED

from .freeze_shared_ontology import ROOT,read,write
from .review_samples import ReadOnlyRepository
from .shared_coverage_review import file_hash
from .regression_gate import request_accounting
from review_agent.contracts import sha256_json

SOURCES=[ROOT/'outputs'/p for p in ('technical_smoke_20261118','technical_smoke_20261118_v2','technical_smoke_20261118_v3')]
OUT=ROOT/'outputs/technical_smoke_20261118_planner_fix'


def run():
    from review_agent.repository import ReviewRepository
    from review_agent.agent_state import AgentState
    from review_agent.action_policy import ActionPolicy
    from review_agent.planner_view import compact_planner_payload
    from review_agent.planner_requests import proposal_for,request,current_action
    from review_agent.model_settings import create_planner_client,configured_planner
    from runs.regress_v8 import fingerprints,configuration
    if OUT.exists():raise ValueError('already launched; inspect the original receipt')
    prior=[];known_tokens=0
    for folder in SOURCES:
        with ReadOnlyRepository(folder/'review.sqlite3').connect() as c:
            accounting=request_accounting(c)
            assert accounting['requests_settled']
            rows=c.execute('SELECT result_json FROM request_attempts').fetchall()
            prior+=list(rows)
            known_tokens+=sum(sum(json.loads(r[0]or'{}').get(k)or 0 for k in ('input_tokens','output_tokens')) for r in rows)
    assert len(prior)==13 and known_tokens<300000
    source=ReadOnlyRepository(SOURCES[-1]/'review.sqlite3')
    cid=next(c['candidate']['candidate_id'] for c in read(SOURCES[-1]/'cases.json') if c['sample_id']=='PILOT-009')
    audit=source.get_audit(cid)
    step=next(s for s in audit['planner_steps'] if s.get('request_proposal'))
    original=step['request_proposal']['payload'];compact=compact_planner_payload(original)
    assert compact['candidate']==original['candidate'] and compact['evidence_catalog']==original['evidence_catalog']
    for old,new in zip(original['current_validation']['votes'],compact['current_validation']['votes']):
        assert old.get('counter_evidence_ids')==new.get('counter_evidence_ids')
    source_checkpoint=source.get_agent_checkpoint(audit['audit_id'])
    state=AgentState(**deepcopy(source_checkpoint['state']))
    state.audit_id='technical_planner_view_'+uuid4().hex
    state.context['planner_compact_view']=True
    # An isolated task based on historical observations, not a resumed audit.
    compact['audit_id']=state.audit_id
    compact['technical_replay']={'source_audit_id':audit['audit_id'],'source_opinions_are_historical':True,
        'action_will_be_executed':False,'is_new_semantic_review':False}
    config=configuration(profile='weighted_diagnostic',directory=OUT)
    policy=ActionPolicy({**config['planner'],'enable_resolution':False,
        'enable_dispute_review':bool((config.get('dispute_review')or{}).get('enabled'))});tools=policy.definitions()
    proposal=proposal_for(state,compact,tools)
    watched=read(SOURCES[-1]/'baseline.json')['watched_hashes']
    watched={**watched,**{str(p):file_hash(p) for folder in SOURCES for p in folder.iterdir() if p.is_file()}}
    assert all(file_hash(p)==h for p,h in watched.items())
    OUT.mkdir()
    write(OUT/'protocol.json',{'version':'compact_planner_technical_check_v1','prior_calls':13,'maximum_new_calls':1,
        'cumulative_call_ceiling':20,'known_token_stop':300000,'prior_known_tokens':known_tokens,
        'caller_wait_seconds':90,'source_audit_id':audit['audit_id'],'source_actions_or_votes_reused_as_new_votes':False,
        'action_will_be_executed':False,'expected_result':'a legal state-bound action, no semantic acceptance requirement'})
    write(OUT/'input_comparison.json',{'original_input_hash':sha256_json(original),
        'compact_input_hash':sha256_json(compact),'sizes':compact['input_size'],'candidate_and_evidence_unchanged':True,
        'material_counter_evidence_ids_unchanged':True})
    write(OUT/'baseline.json',{'watched_hashes':watched})
    write(OUT/'proposal.json',proposal)
    frozen=fingerprints();write(OUT/'code_fingerprints.json',frozen)
    with ZipFile(OUT/'execution_code.zip','x',ZIP_DEFLATED) as z:
        for name in frozen:
            if Path(name).suffix in {'.py','.txt'} or name.startswith('specs/'):z.write(ROOT/name,name)
    repo=ReviewRepository(OUT/'review.sqlite3');repo.initialize()
    client=create_planner_client(config);settings=configured_planner(config)
    voter={**{k:settings[k] for k in ('request_timeout_seconds','http_timeout_seconds','max_output_tokens')},
        'id':'planner','model_id':client.model_id,'model_family':client.model_family,'model_identity':client.model_id.casefold(),
        'request_channel':'planner','client':client}
    result=request(repo,voter,proposal,90)
    action,error=current_action(result,proposal,state,validator=lambda a:policy.validate_request(a,state))
    write(OUT/'result.json',result)
    with repo.connect() as c:
        accounting=request_accounting(c);rows=c.execute('SELECT result_json FROM request_attempts').fetchall()
    assert len(rows)<=1
    total_tokens=known_tokens+sum(sum(json.loads(r[0]or'{}').get(k)or 0 for k in ('input_tokens','output_tokens')) for r in rows)
    unchanged=all(file_hash(p)==h for p,h in watched.items());code_same=fingerprints()==frozen
    report={'new_provider_calls':len(rows),'cumulative_provider_calls':len(prior)+len(rows),
        'cumulative_known_tokens':total_tokens,'response_status':result['response_status'],'action':action,'action_error':error,
        'action_executed':False,'used_as_review_vote':False,'all_source_files_unchanged':unchanged,
        'code_unchanged_during_call':code_same,'reconciled_previous_requests_settled':True,**accounting,
        'technical_check_passed':bool(action is not None and result['response_status']=='ok' and accounting['requests_settled'] and unchanged and code_same),
        'input_sizes':compact['input_size'],'production_written':False,'formal_experiment_started':False}
    assert len(prior)+len(rows)<=20 and unchanged and code_same
    write(OUT/'report.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':run()
