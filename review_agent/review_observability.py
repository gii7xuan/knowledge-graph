"""Versioned diagnostic projections on existing votes; no new votes or write authority."""
from copy import deepcopy
from collections import Counter
import json
import re
from .contracts import sha256_json

VERSION='review_outcome_v3'
VALIDATION_VERSION='vote_validation_v3'
AXES=('source','expression','mapping','scope')
DEPENDENCIES={
 'source':{'triple_support','entity_reference','relation_semantics','relation_direction','qualifiers','evidence_sufficiency','counterevidence'},
 'expression':{'triple_support','entity_reference','relation_semantics','relation_direction','qualifiers'},
 'mapping':{'mapping'},'scope':{'knowledge_scope'}}


def policy():
    from .review_axes import VERSION as axes_version
    return {'version':VERSION,'validation_version':VALIDATION_VERSION,'axes_version':axes_version,
        'partial_dimensions':'preserve_observations_never_replace_full_committee',
        'selection':'first_record_per_frozen_seat','writes':'none_without_existing_admission_contract'}


def redact(value):
    if isinstance(value,dict):return {k:('[REDACTED]' if re.search(r'api.?key|authorization|password|credential|secret|access.?token',str(k),re.I) else redact(v)) for k,v in value.items()}
    if isinstance(value,list):return [redact(v) for v in value]
    if isinstance(value,str):
        value=re.sub(r'(?i)(Bearer\s+)\S+',r'\1[REDACTED]',value)
        return re.sub(r'\bsk-[A-Za-z0-9_-]{8,}\b','[REDACTED]',value)
    return value


def _axes_for_path(parts):
    if not parts:return set(AXES)
    if parts[0]=='dimensions':
        return {a for a,names in DEPENDENCIES.items() if len(parts)<2 or parts[1] in names}
    if parts[0]=='expression_findings':return {'source','expression'}
    if parts[0] in {'evidence_assessments','primary_evidence_ids','counterevidence_reviewed_ids'}:return {'source'}
    if parts[0] in {'requested_context','suggested_revision','schema_change_hint'}:return set()
    return set(AXES)


def _axes_for_error(error):
    text=str(error).lower()
    if any(s in text for s in ('meaning','finding','representation','normalization','field plan')):return {'source','expression'}
    if any(s in text for s in ('mapping','relation id','formal id','ontology context')):return {'mapping'}
    if 'knowledge_scope' in text:return {'scope'}
    names={name for group in DEPENDENCIES.values() for name in group if name in text}
    if names:return {a for a,group in DEPENDENCIES.items() if group & names}
    if any(s in text for s in ('counter','primary_evidence','evidence role','supporting evidence','source support')):return {'source'}
    return set(AXES)


def error_item(category,message,*,path=None,axes=AXES,expected=None,actual=None,stage=None):
    return {'category':category,'stage':stage or category,'path':path,'message':redact(str(message)),
        'axes':sorted(axes),'expected':expected,'actual_type':actual}


def assess_vote(vote,proposal,*,frozen_request=None):
    """Keep reliable axes from a locally bad dimension; identity failures fence all axes."""
    from .validator_contracts import contract_for,validate_vote,proposal_errors,mapping_reference_errors,prompt_for
    from .validators import _parse_content
    from .response_corrections import schema_issues
    from .review_axes import axis_opinion
    errors=[];blocked={a:[] for a in AXES};version=vote.get('response_contract_version');inference=None
    if not version and frozen_request and frozen_request.get('unique_binding_verified') is True and frozen_request.get('proposal_digest')==proposal.get('proposal_digest') and frozen_request.get('response_contract_version')==proposal.get('response_contract_version'):
        version=frozen_request['response_contract_version'];inference='unique_frozen_request_binding'
        vote={**vote,'response_contract_version':version}
    global_ok=True;contract=None;raw=None
    if not version:errors.append(error_item('version','response version unknown; no unique verified frozen request binding'));global_ok=False
    if version!=proposal.get('response_contract_version'):
        errors.append(error_item('version','response/request contract version mismatch',path='/response_contract_version'))
        global_ok=False
    try:contract=contract_for(version) if version else None
    except ValueError as exc:errors.append(error_item('version',exc));global_ok=False
    if contract is None:global_ok=False
    expected={'task_type':'triple_review','task_id':proposal.get('candidate',{}).get('candidate_id'),'object_revision':proposal.get('candidate',{}).get('revision'),
        'source_snapshot_hash':proposal.get('candidate',{}).get('source_snapshot_id'),
        'audit_id':proposal.get('audit_id'),'round_id':proposal.get('round_id'),'proposal_digest':proposal.get('proposal_digest'),
        'evidence_hash':proposal.get('evidence_bundle',{}).get('evidence_hash')}
    for key in ('document_version','source_index_hash','ontology_version','ontology_release_id','profile_hash','committee_version','committee_policy_hash','prompt_version','correction_policy_hash','ontology_context_hash','ontology_context_policy_hash'):
        if key in proposal:expected[key]=proposal[key]
    for key,value in expected.items():
        if vote.get(key)!=value:
            errors.append(error_item('provenance','frozen binding mismatch: '+key,path='/'+key));global_ok=False
    member=next((m for m in proposal.get('committee_members',[]) if m['id']==vote.get('voter_id')),None)
    if not member or any(vote.get(k)!=member.get(k) for k in ('model_id','model_family')) or (member and str(vote.get('model_identity') or '').casefold()!=str(member.get('model_identity') or member.get('model_id') or '').casefold()):
        errors.append(error_item('provenance','seat/model identity differs from frozen committee'));global_ok=False
    if vote.get('outcome_unknown') or vote.get('response_status') in {'timeout','api_error','outcome_unknown'}:
        errors.append(error_item('transport',vote.get('error') or vote.get('error_code') or 'request outcome unavailable'));global_ok=False
    if vote.get('response_status')=='output_incomplete' or vote.get('finish_reason') in {'length','max_tokens','incomplete'}:
        errors.append(error_item('output_incomplete','truncated or interrupted output cannot be a full vote'));global_ok=False
    if vote.get('raw_response') is not None or vote.get('corrected_response') is not None or vote.get('response_status')=='ok':
        try:
            raw=deepcopy(vote.get('corrected_response')) if vote.get('corrected_response') is not None else _parse_content(vote.get('raw_response'))
        except (ValueError,TypeError):
            if contract and vote.get('response_status')=='ok':raw=contract.raw_from_vote(vote)
            else:errors.append(error_item('parse','raw response is not a complete JSON object'));global_ok=False
    elif global_ok:
        errors.append(error_item('parse','response body unavailable'));global_ok=False
    if not isinstance(raw,dict):global_ok=False
    if global_ok:
        try:
            prompt_for(proposal)
            if vote.get('response_origin')=='field_patch_merge':
                trace_errors=contract.validate_correction_trace(vote)
                if trace_errors:raise ValueError('; '.join(trace_errors))
        except (ValueError,KeyError,TypeError) as exc:
            errors.append(error_item('provenance',exc));global_ok=False
    if global_ok and raw.get('response_contract_version') not in (None,version):
        errors.append(error_item('version','declared response version differs from request',path='/response_contract_version'));global_ok=False
    if global_ok and 'dimensions' in contract.RESPONSE_SCHEMA.get('properties',{}):
        structural=schema_issues(raw,contract.RESPONSE_SCHEMA)
        for issue in structural:
            parts=issue['parts'];axes=_axes_for_path(parts)
            item=error_item('schema',issue['kind'],path='/'+ '/'.join(map(str,parts)),axes=axes,
                expected=issue['schema'].get('type') or issue['schema'].get('enum'),
                actual=_type_at(raw,parts))
            errors.append(item)
            for a in axes:blocked[a].append(item['message'])
        if not structural:
            for message in contract.validate_response(raw)+proposal_errors(raw,proposal):
                axes=_axes_for_error(message);item=error_item('provenance' if 'references relation ids' in str(message).lower() else 'semantic_consistency',message,axes=axes)
                errors.append(item)
                for a in axes:blocked[a].append(item['message'])
        # These checks run even when an unrelated mapping/scope field is malformed.
        from .response_corrections import _conflicts
        for message in _conflicts(raw):
            errors.append(error_item('semantic_consistency',message,axes={'source'}))
            blocked['source'].append(message)
        if not blocked['expression']:
            try:
                finding_check=getattr(contract,'finding_errors',None)
                if finding_check is None:
                    raise ValueError('legacy response has no independently recorded expression findings')
                from .field_spec import finding_errors as field_errors
                from .review_axes import normalization_errors
                checks=finding_check(raw)+(field_errors(raw,proposal) if version=='validator_response_v8' else normalization_errors(raw,proposal))
                for message in checks:
                    errors.append(error_item('semantic_consistency',message,axes={'source','expression'}))
                    for axis in ('source','expression'):blocked[axis].append(message)
            except (KeyError,TypeError,ValueError) as exc:
                for axis in ('source','expression'):blocked[axis].append(str(exc))
        available={e['evidence_id'] for e in proposal.get('evidence_bundle',{}).get('items',[]) if e.get('anchor_valid')}
        dimensions=raw.get('dimensions') if isinstance(raw.get('dimensions'),dict) else {}
        for axis,names in DEPENDENCIES.items():
            for name in names:
                part=dimensions.get(name)
                if not isinstance(part,dict):blocked[axis].append('dimension missing: '+name);continue
                refs=part.get('evidence_ids') or []
                if not isinstance(refs,list) or any(not isinstance(e,str) or e not in available for e in refs):blocked[axis].append('unverified evidence: '+name)
                if part.get('status') not in {'unknown','unresolved','unassigned','context_incomplete'} and not refs:blocked[axis].append('missing reviewed source citations: '+name)
        from evidence.units import can_support
        support={a.get('evidence_id') for a in raw.get('evidence_assessments',[]) if isinstance(a,dict) and a.get('role')=='support'} if isinstance(raw.get('evidence_assessments'),list) else set()
        if support-available or any(e['evidence_id'] in support and not can_support(e) for e in proposal['evidence_bundle']['items']):blocked['source'].append('support references invalid or non-substantive source')
        for key in ('primary_evidence_ids','counterevidence_reviewed_ids'):
            refs=raw.get(key)
            if not isinstance(refs,list) or any(not isinstance(e,str) or e not in available for e in refs):blocked['source'].append('unverified citations: '+key)
        if not blocked['source']:
            if axis_opinion(raw,'source')['status']=='supported' and (not raw.get('primary_evidence_ids') or not set(raw['primary_evidence_ids'])<=support):
                blocked['source'].append('source support requires a nominated positive evidence set')
        if not blocked['mapping']:
            for message in mapping_reference_errors(raw,proposal):
                blocked['mapping'].append(message)
                errors.append(error_item('provenance',message,axes={'mapping'}))
    else:
        for a in AXES:blocked[a].append('response identity/completeness/provenance unavailable' if not global_ok else 'legacy contract has no independently recorded dimensions')
    observations={}
    for axis in AXES:
        value=None
        if global_ok and not blocked[axis]:
            try:value=axis_opinion(raw,axis)
            except (KeyError,TypeError,ValueError):blocked[axis].append('axis dependencies incomplete')
        observations[axis]={'usable':value is not None,'opinion':value,'missing_reasons':sorted(set(blocked[axis]))}
    complete_errors=[]
    if global_ok:
        try:complete_errors=validate_vote(vote,version)+proposal_errors(contract.raw_from_vote(vote),proposal) if vote.get('response_status')=='ok' else [vote.get('error') or 'response not accepted']
        except (ValueError,TypeError,KeyError):complete_errors=['whole response contract unavailable']
        if not complete_errors:
            from evidence.verification import verify_vote_references
            verified=verify_vote_references(vote,proposal['evidence_bundle']['items'])
            if verified.get('response_status')!='ok':complete_errors.append(verified.get('error') or 'source reference verification failed')
    eligible=global_ok and vote.get('response_status')=='ok' and not complete_errors
    for message in complete_errors:
        if not any(e['message']==message for e in errors):errors.append(error_item('semantic_consistency',message,axes=_axes_for_error(message)))
    if not eligible and not errors:errors.append(error_item('schema',vote.get('error') or 'response not eligible'))
    for axis, reasons in blocked.items():
        for reason in set(reasons):
            if reason=='response identity/completeness/provenance unavailable':continue
            if not any(e['message']==reason for e in errors):errors.append(error_item('provenance' if reason.startswith(('unverified','missing reviewed','support references invalid')) else 'semantic_consistency',reason,axes={axis}))
    return {'validation_version':VALIDATION_VERSION,'voter_id':vote.get('voter_id'),'response_contract_version':version,
        'version_inference':inference,'global_trustworthy':global_ok,'whole_vote_eligible':bool(eligible),
        'raw_response_hash':sha256_json(vote.get('raw_response')),'normalized_response_hash':sha256_json(raw) if isinstance(raw,dict) else None,
        'original_attempt_id':(vote.get('correction_trace') or {}).get('original_attempt_id') or vote.get('attempt_id'),
        'attempt_id':vote.get('attempt_id'),'request_key':vote.get('request_key'),'cache_source':redact(vote.get('cache_source')),
        'errors':errors,'axis_observations':observations}


def _type_at(value,parts):
    try:
        for part in parts:value=value[part]
    except (KeyError,IndexError,TypeError):return 'missing'
    return 'null' if value is None else type(value).__name__


def summarize_dimensions(votes,proposal,committee):
    from .scope_policy import classification_decision,DEFAULT_POLICY
    roster=set(committee.voter_ids);selected={};reports={};duplicates=0;identities=set()
    for vote in votes:
        voter=vote.get('voter_id')
        if voter not in roster:continue
        if voter in selected:duplicates+=1;continue
        selected[voter]=vote;report=assess_vote(vote,proposal)
        model_ids={str(x).strip().casefold() for x in (vote.get('model_identity') or vote.get('model_id'),vote.get('returned_model_id')) if x}
        if model_ids & identities:
            report['whole_vote_eligible']=False
            for observation in report['axis_observations'].values():observation.update(usable=False,opinion=None,missing_reasons=['duplicate model identity'])
        identities.update(model_ids);reports[voter]=report
    result={}
    for axis in AXES:
        observations=[{'voter_id':v,'model_family':selected[v].get('model_family'),**r['axis_observations'][axis]} for v,r in reports.items()]
        usable=[o for o in observations if o['usable']];families={o['model_family'] for o in usable}-{None,''}
        covered=len(usable)==len(roster) and len(families)>=committee.minimum_distinct_families
        states={o['opinion'].get('knowledge_scope') if axis=='scope' else o['opinion']['status'] for o in usable}
        observed=next(iter(states)) if len(states)==1 else None
        status=observed if covered and observed else 'unclear' if axis=='scope' else 'unresolved'
        if axis=='scope':status=classification_decision(observed,covered,proposal.get('review_scope_policy') or DEFAULT_POLICY)['status']
        if axis=='mapping' and proposal.get('mapping_preflight',{}).get('judgment_ready') is False and status!='conflict':status='context_incomplete'
        if axis=='mapping' and status=='valid' and proposal.get('ontology_check',{}).get('status')!='pass':status='context_incomplete'
        result[axis]={'status':status,'observed_result':observed,'executed':any(o['opinion'] is not None for o in observations),
            'seat_coverage_complete':covered,'judgment_complete':covered and status not in {'unresolved','unclear','context_incomplete'},
            'valid_seats':len(usable),'required_seats':len(roster),'missing_voter_ids':sorted(roster-{o['voter_id'] for o in usable}),
            'has_conflict':len(states)>1,'observations':observations}
    return {'version':VERSION,'dimension_results':result,'vote_validation':reports,'duplicate_records_ignored':duplicates,
        'whole_vote_eligible_voter_ids':sorted(v for v,r in reports.items() if r['whole_vote_eligible'])}


def outcome(proposal,votes,committee,*,audit_status='completed',stop_reason=None,context=None,decision=None):
    context=context or {};decision=decision or {};report=summarize_dimensions(votes,proposal,committee)
    dimensions=report['dimension_results'];blockers=[]
    complete=set(report['whole_vote_eligible_voter_ids'])==set(committee.voter_ids)
    if not complete:blockers.append('required_committee_incomplete')
    guard=proposal.get('ontology_check') or {}
    if guard.get('status')!='pass':blockers.append('ontology_guard_not_passed')
    assigned=proposal.get('candidate',{}).get('triple',{}).get('relation_id')
    if not assigned or any((v.get('dimensions') or {}).get('mapping',{}).get('relation_id')!=assigned
            for v in votes if v.get('voter_id') in report['whole_vote_eligible_voter_ids']):blockers.append('formal_mapping_not_bound_to_candidate')
    if proposal.get('candidate',{}).get('contract_version')=='extraction_candidate_v4' and proposal['candidate'].get('fact_key_state')!='canonical':blockers.append('candidate_identity_unresolved')
    from .revision_patch import expression_diagnostics
    if proposal.get('candidate') and expression_diagnostics(proposal['candidate']).get('expression_complete') is False:blockers.append('candidate_expression_incomplete')
    admission_gaps={'critical_issues_unresolved','protected_evidence_omitted','unreviewed_discovered_evidence',
        'validation_missing','version_stale','ontology_not_checked','identity_unresolved','unresolved_counterevidence',
        'unresolved_support_conflict','need_more_evidence','ontology_disagreement','validation_budget_exhausted'}
    for key,value in (context.get('final_gaps') or {}).items():
        if value and key in admission_gaps:blockers.append('gap:'+key)
    expected={'source':'supported','expression':'standard','mapping':'valid','scope':'include'}
    for axis,wanted in expected.items():
        if dimensions[axis]['status']!=wanted:blockers.append(axis+':'+dimensions[axis]['status'])
    if any(d['has_conflict'] for d in dimensions.values()):blockers.append('unresolved_dimension_disagreement')
    if any(i.get('critical') and i.get('status','open')!='resolved' for i in context.get('issue_history',[])):blockers.append('critical_issue_unresolved')
    if any(d.get('status')=='open' for d in (context.get('dispute_report') or decision.get('consensus',{}).get('dispute_report') or {}).get('disputes',[])):blockers.append('unresolved_claim_dispute')
    execution=('not_started' if audit_status=='not_started' else 'running' if audit_status=='running' else
        'timed_out' if audit_status=='timeout' else 'interrupted' if audit_status in {'interrupted','superseded'} else
        'paused' if audit_status=='paused' else 'failed' if audit_status=='failed' else 'completed' if complete else 'partial_failure')
    if execution not in {'completed','partial_failure'}:blockers.append('execution:'+execution)
    recommendation=('exclude' if dimensions['scope']['status']=='exclude' else 'admit' if not blockers else
        'revise' if dimensions['expression']['status'] in {'semantic_revision','lossless_normalization'} or dimensions['source']['status']=='contradicted' else
        'wait_mapping' if dimensions['source']['status']=='supported' and dimensions['expression']['status']=='standard' and dimensions['mapping']['status']!='valid' else
        'insufficient_evidence' if dimensions['source']['status']=='insufficient' else 'manual_review')
    from .mapping_trace import process
    return {**report,'policy_hash':sha256_json(policy()),'execution_status':execution,'execution_reason':stop_reason or context.get('agent_stop_reason') or context.get('execution_failure',{}).get('reason_code') or audit_status,
        'mapping_process':process(proposal,votes),
        'review_completed':complete and all(d['judgment_complete'] for d in dimensions.values()),
        'recommendation':recommendation,'action_taken':'none','blocking_reasons':sorted(set(blockers)),
        'writes_authorized':False,'derivation':'current_frozen_input_only'}
