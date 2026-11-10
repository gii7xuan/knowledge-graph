"""Explicit, candidate-bound operator actions for revision and focused review."""
from .contracts import sha256_json
from .repository import ValidationError,ConflictError


def selected(service,candidate_id,payload):
    current=service.repository.get_candidate(candidate_id)
    if payload.get('revision')!=current['revision'] or payload.get('expected_row_version')!=current['row_version']:
        raise ConflictError('candidate revision or row version changed')
    candidate=service.repository.get_revision(candidate_id,current['revision'])
    audit=service.repository.get_audit(candidate_id,str(payload.get('audit_id') or ''))
    proposal=next((p for p in audit['rounds'] if p['proposal_digest']==payload.get('proposal_digest') and
        p['evidence_bundle']['evidence_hash']==payload.get('evidence_hash')),None)
    if not proposal or proposal['candidate']!=candidate:raise ValidationError('action requires the exact current frozen candidate and evidence packet')
    return current,candidate,audit,proposal


def propose_split(service,candidate_id,payload):
    from .revision_service import RevisionService
    from .admission import load_admission_artifacts
    current,candidate,audit,proposal=selected(service,candidate_id,payload)
    _,document=load_admission_artifacts(service.repository,candidate)
    protected=sorted({eid for v in audit['votes'] if v.get('proposal_digest')==proposal['proposal_digest'] and v.get('response_status')=='ok'
        for eid in [*v.get('supporting_evidence_ids',[]),*v.get('counter_evidence_ids',[])]})
    reason=payload.get('reason')
    if not isinstance(reason,str) or not reason.strip() or len(reason)>800:raise ValidationError('a bounded split reason is required')
    return RevisionService(service.repository).propose_split(candidate,current['row_version'],payload.get('children'),reason,
        proposal['evidence_bundle'],document,protected_ids=protected,source_audit_id=audit['audit_id'])


def submit(service,payload):
    action=payload.get('review_action')
    from .revision_service import RevisionService
    if action=='review_revision':
        draft=RevisionService(service.repository).draft(str(payload.get('draft_id') or ''))
        if draft['status'] not in {'proposed','review_incomplete'}:raise ConflictError('draft is not available for independent review')
    elif action=='review_split':
        group=RevisionService(service.repository).split(str(payload.get('group_id') or ''))
        if group['status'] in {'stale','applied'}:raise ConflictError('split group baseline is unavailable')
    elif action=='review_dispute':
        selected(service,str(payload.get('candidate_id') or ''),payload)
    else:raise ValidationError('unknown bounded review action')
    data={k:payload[k] for k in ('review_action','draft_id','group_id','candidate_id','revision','expected_row_version','audit_id','proposal_digest','evidence_hash','dispute_id') if k in payload}
    return service.job_runner.submit(data) if service.job_runner else service.repository.create_job('audit',data)


def execute(service,payload):
    from .revision_service import RevisionService
    from .cli import _configured_validators
    from .consensus import Committee
    from .case_budget import CaseBudget
    from .repository import ReviewRepository
    repository=ReviewRepository(service.repository.database_path)
    if payload.get('_job_id'):repository.job_lease_binding={'job_id':payload['_job_id'],'owner':payload['_job_owner'],'lease_token':payload['_job_lease_token']}
    revisions=RevisionService(repository);action=payload['review_action']
    if action=='review_split':
        group=revisions.split(payload['group_id']);drafts=group['drafts'];candidate_id=group['parent_candidate_id']
    elif action=='review_revision':
        drafts=[revisions.draft(payload['draft_id'])];candidate_id=drafts[0]['candidate_id']
    else:candidate_id=payload['candidate_id'];drafts=[]
    voters=_configured_validators(service.config)
    if not voters:raise ValidationError('no configured independent review committee')
    settings=service.config.get('consensus') or {}
    committee=Committee([v['id'] for v in voters],int(settings.get('required_votes',3)),int(settings.get('minimum_distinct_families',3)))
    if len(voters)!=int(settings.get('committee_size',len(voters))):raise ValidationError('complete configured committee required')
    budget=CaseBudget(service.config.get('budget') or {},repository,[repository.ensure_case(candidate_id,service.config.get('case_budget'))],task_type=action,task_id=candidate_id)
    if action=='review_dispute':
        _,candidate,audit,base=selected(service,candidate_id,payload)
        from .disputes import build_report
        report=build_report(base,audit['votes'],(base.get('consensus') or {}).get('eligible_voter_ids'))
        dispute=next((d for d in report['disputes'] if d['dispute_id']==payload.get('dispute_id')),None)
        if not dispute:raise ValidationError('dispute does not belong to the selected frozen proposal')
        from .admission import load_admission_artifacts
        release,document=load_admission_artifacts(repository,candidate)
        from evidence.hierarchical_retriever import retrieve_more
        from evidence.verification import make_evidence_bundle,verify_evidence_bundle
        items=list(base['evidence_bundle']['items']);trace=[]
        query=' '.join(str(candidate['triple'].get(k) or '') for k in ('head','surface_relation','tail'))
        for scope in ('current_chunk','same_section','parent_section'):
            found=retrieve_more(candidate,document,{'scope':scope,'query':query,'limit':6},[e['evidence_id'] for e in items])
            items.extend(found['items']);trace.append({'scope':scope,'added':len(found['items'])})
            if len(items)>len(base['evidence_bundle']['items']):break
        bundle=verify_evidence_bundle(make_evidence_bundle(items,[t['scope'] for t in trace]),document,candidate)
        ontology=base.get('ontology_context');ready=False
        if dispute['check']=='mapping':
            from .ontology_guard import OntologyGuard,load_snapshot
            from .ontology_context import select_context,policy_for
            from .mapping_preflight import prepare,policy_for as prep_policy
            guard=OntologyGuard(load_snapshot(release.ontology));cp=policy_for(service.config)
            ontology=select_context(guard,candidate,bundle,cp)
            ontology,preflight=prepare(guard,candidate,bundle,ontology,cp,prep_policy({}));ready=preflight['judgment_ready']
        from .dispute_review import review
        result=review(repository,base,dispute,bundle,voters,committee,budget,ontology,ready)
        return {**result,'retrieval_trace':trace,'whole_candidate_admitted':False}
    results=[]
    for draft in drafts:
        if payload.get('_job_id') and repository.get_job(payload['_job_id']).get('cancel_requested'):raise ValidationError('review job cancelled')
        results.append(revisions.review(draft['draft_id'],voters,committee,budget))
    return {'results':results,'group_id':payload.get('group_id'),'candidate_unchanged':True,'application_requires_explicit_action':True}
