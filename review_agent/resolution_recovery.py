"""Zero-call projections of original receipts; never execute a recovered planner action."""
from copy import deepcopy
import json
from contextlib import contextmanager
from pathlib import Path
from .repository import ReviewRepository


class ReadView(ReviewRepository):
    """Reuse the monitor's existing read transaction without migration or writes."""
    def __init__(self,connection):
        self.connection=connection
        self.database_path=Path(connection.execute('PRAGMA database_list').fetchone()[2])

    @contextmanager
    def connect(self):
        yield self.connection

    def transaction(self,*args,**kwargs):
        raise PermissionError('resolution projection is read-only')


def hydrate(repository,audit,context=None):
    context=deepcopy(context if context is not None else audit.get('context') or {})
    context.setdefault('original_receipt_results',[])
    for vote in audit.get('votes',[]):
        if vote.get('response_status')=='ok' or not vote.get('attempt_id'):continue
        with repository.connect() as c:
            row=c.execute('SELECT state,result_json FROM request_attempts WHERE attempt_id=?',(vote['attempt_id'],)).fetchone()
        if row and row['state']=='completed':
            receipt=json.loads(row['result_json'] or '{}')
            if receipt not in context['original_receipt_results']:context['original_receipt_results'].append(receipt)
    with repository.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        focused=list(c.execute('SELECT proposal_json,result_json FROM dispute_focus_reviews WHERE audit_id=?',(audit['audit_id'],))) if 'dispute_focus_reviews' in tables else []
        semantic=list(c.execute('SELECT proposal_json,result_json FROM semantic_recheck_tasks WHERE audit_id=?',(audit['audit_id'],))) if 'semantic_recheck_tasks' in tables else []
        for kind,rows in (('focused',focused),('semantic',semantic)):
            for row in rows:
                proposal=json.loads(row['proposal_json']);result=json.loads(row['result_json'] or '{}')
                receipts=[json.loads(r[0]) for r in c.execute("SELECT result_json FROM request_attempts WHERE "
                    "json_extract(metadata_json,'$.proposal.proposal_digest')=? ORDER BY started_at",
                    (proposal['proposal_digest'],)) if r[0]]
                latest={r['voter_id']:r for r in receipts}
                if kind=='focused':
                    votes={v['voter_id']:v for v in result.get('votes',[])};votes.update(latest)
                    result.update(review_proposal=proposal,dispute_id=proposal['dispute']['dispute_id'],votes=list(votes.values()))
                    context.setdefault('focused_review_results',[])
                    context['focused_review_results']=[r for r in context['focused_review_results'] if r.get('dispute_id')!=result['dispute_id']]+[result]
                else:
                    if latest:result={**latest.get(proposal['voter_id'],result),'review_proposal':proposal}
                    if result:context.setdefault('semantic_recheck_results',{})[proposal['voter_id']]=result
    return context


def project_context(repository,audit,committee):
    from .agent_state import AgentState
    from .resolution_state import from_context,update_issue_ledger
    from .action_policy import ActionPolicy
    from .resolution_status import technical_tasks
    saved=(audit.get('agent_state') or {}).get('state') or {}
    context=deepcopy({**audit.get('context',{}),**saved.get('context',{})})
    if not context.get('resolution_snapshots_enabled'):return context
    selected=(context.get('dispute_baseline') or context.get('resolution_baseline') or {}).get('proposal_digest')
    base=next((p for p in audit['rounds'] if p['proposal_digest']==selected),audit['rounds'][-1] if audit['rounds'] else None)
    if not base:return context
    votes=[v for v in audit['votes'] if v['proposal_digest']==base['proposal_digest']]
    context=hydrate(repository,audit,context)
    context.update(issue_history=deepcopy(saved.get('issues',context.get('issue_history',[]))),
        resolution_candidate=deepcopy(base['candidate']),resolution_bundle=deepcopy(saved.get('bundle') or context.get('resolution_bundle') or base['evidence_bundle']))
    from .contracts import sha256_json
    for result in context.get('focused_review_results',[]):
        proposal=result.get('review_proposal') or {};items=proposal.get('evidence_bundle',{}).get('items',[])
        required={e['evidence_id'] for e in [*context['resolution_bundle'].get('items',[]),*base['evidence_bundle']['items']]}
        if (proposal.get('candidate_hash')==sha256_json(base['candidate']) and proposal.get('source_proposal_digest')==base['proposal_digest']
                and sha256_json({k:v for k,v in proposal.items() if k!='proposal_digest'})==proposal.get('proposal_digest')
                and required<={e['evidence_id'] for e in items}):context['resolution_bundle']=deepcopy(proposal['evidence_bundle'])
    context['resolution_ontology_context']=(saved.get('ontology_lookup') or {}).get('retrieved_context',context.get('resolution_ontology_context',base.get('ontology_context')))
    tasks=technical_tasks(repository,audit['audit_id'])
    technical_codes={'planner_failure','planner_budget','planner_unavailable','minimum_review_incomplete','token_budget','tool_budget','validation_budget'}
    for issue in context['issue_history']:
        if issue['code'] in technical_codes and issue.get('status','open')!='resolved':
            issue['semantic_critical']=False;issue['critical']=False
            tasks.append({'task_type':'execution_obligation','issue_id':issue['issue_id'],
                'execution_unknown':False,'usage_unknown':False,'required_action':'technical_recovery','automatic_resend':False})
    if audit['status'] not in {'completed','superseded'}:
        tasks.append({'task_type':'audit_execution','audit_id':audit['audit_id'],'status':audit['status'],
            'execution_unknown':False,'usage_unknown':False,'required_action':'technical_recovery','automatic_resend':False})
    snapshot=from_context(base,votes,committee,context)
    state=AgentState(**deepcopy(saved)) if saved else AgentState(deepcopy(base['candidate']),context,audit['audit_id'])
    state.context=context;state.issues=context['issue_history'];state.bundle=context['resolution_bundle']
    state.ontology_checked=bool(saved.get('ontology_checked',False))
    update_issue_ledger(state,base,votes,committee,snapshot)
    context['resolution_snapshot']=from_context(base,votes,committee,context)
    from .consensus import aggregate_votes
    from .resolution_state import apply_consensus
    snapshot=context['resolution_snapshot']
    state.consensus=apply_consensus(aggregate_votes(votes,committee,proposal=base,proposal_digest=base['proposal_digest']),snapshot)
    if (set(snapshot['whole_vote_eligible_voter_ids'])==set(committee.voter_ids)
            and state.bundle.get('evidence_hash')==base['evidence_bundle']['evidence_hash']
            and state.current_validation() is None):
        consensus=state.consensus
        state.validations[state.validation_key()]={'proposal_digest':base['proposal_digest'],'round_id':base['round_id'],
            'votes':votes,'consensus':consensus,'evidence_hash':state.bundle.get('evidence_hash')}
        state.consensus=consensus
    context['final_gaps']=ActionPolicy({}).final_gaps(state)
    context.update(technical_recovery_tasks=tasks,semantic_recovery_projection=True,
                   original_decisions_changed=False,model_calls_during_projection=0)
    return context
