"""Derive scoped resolutions without rewriting a single original committee opinion."""
from copy import deepcopy
from .contracts import sha256_json

VERSION = 'review_resolution_snapshot_v1'
CHECK_AXES = {'triple_support': {'source'}, 'entity_reference': {'source', 'expression'},
    'relation_semantics': {'source', 'expression'}, 'relation_direction': {'source', 'expression'},
    'qualifiers': {'source', 'expression'}, 'evidence_sufficiency': {'source'},
    'counterevidence': {'source'}, 'mapping': {'mapping'}, 'knowledge_scope': {'scope'}}


def formal_context_hash(context):
    context=context or {}
    return sha256_json({k:context.get(k) for k in ('properties','classes','source_catalog_hash','ontology_release_id','profile_hash')})


def resolve_review_state(base_proposal, original_votes, focused_results, issue_ledger, committee,
                         *, candidate=None, bundle=None, ontology_context=None, semantic_results=(),original_receipts=()):
    from .review_observability import summarize_dimensions
    from .review_axes import vote_axes
    from .validator_contracts import raw_from_vote
    from .dispute_review import focused_consensus
    from .disputes import build_report,target_for,TYPE_FOR
    candidate = candidate or base_proposal['candidate']
    bundle = bundle or base_proposal['evidence_bundle']
    ontology_context = ontology_context if ontology_context is not None else base_proposal.get('ontology_context')
    original_hash = sha256_json(original_votes)
    effective_votes=deepcopy(original_votes);semantic_ids=[];receipt_ids=[]
    from .review_observability import assess_vote
    for receipt in original_receipts:
        old=next((v for v in original_votes if v.get('attempt_id') and v.get('attempt_id')==receipt.get('attempt_id')),None)
        if not old or old.get('response_status')=='ok' or not assess_vote(receipt,base_proposal)['whole_vote_eligible']:continue
        effective_votes=[deepcopy(receipt) if v.get('voter_id')==receipt['voter_id'] else v for v in effective_votes]
        receipt_ids.append(receipt['attempt_id'])
    for result in semantic_results:
        from .semantic_recheck import valid,binding
        from .validators import _parse_content
        proposal=result.get('review_proposal') or {}
        original=next((v for v in original_votes if v.get('voter_id')==proposal.get('voter_id')),None)
        member=next((v for v in base_proposal.get('committee_members',[]) if v['id']==proposal.get('voter_id')),None)
        if not original or not member or original.get('outcome_unknown') or original.get('response_status')!='invalid_contract':continue
        try:raw_original=_parse_content(original.get('raw_response'))
        except (ValueError,TypeError):continue
        if (proposal.get('base_proposal')!=base_proposal or proposal.get('original_response_hash')!=sha256_json(raw_original)
                or sha256_json({k:v for k,v in proposal.items() if k!='proposal_digest'})!=proposal.get('proposal_digest')
                or any(result.get(k)!=v for k,v in binding(member,proposal).items()) or not valid(result,proposal)):continue
        effective_votes=[deepcopy(result['review_vote']) if v.get('voter_id')==member['id'] else v for v in effective_votes]
        semantic_ids.append(proposal['task_id'])
    report = summarize_dimensions(effective_votes, base_proposal, committee)
    disputes = build_report(base_proposal, effective_votes, report['whole_vote_eligible_voter_ids'])
    targets = {d['dispute_id']: d for d in disputes['disputes']}
    dimensions = deepcopy(report['dimension_results'])
    current = {e['evidence_id']: e for e in bundle.get('items', [])}
    old = {e['evidence_id']: e for e in base_proposal['evidence_bundle']['items']}
    material_keys = ('quote', 'document_id', 'document_version', 'start', 'end', 'source_anchor_kind')
    same_source = all(eid in current and all(current[eid].get(k) == item.get(k) for k in material_keys)
                      for eid, item in old.items())
    same_candidate = sha256_json(candidate) == sha256_json(base_proposal['candidate'])
    same_release = all(candidate.get(k) == base_proposal['candidate'].get(k)
                       for k in ('ontology_release_id', 'ontology_version', 'profile_hash', 'source_index_hash'))
    eligible = set(report['whole_vote_eligible_voter_ids'])
    raw = {v['voter_id']: deepcopy(raw_from_vote(v)) for v in effective_votes if v.get('voter_id') in eligible}
    accepted, rejected, affected, reviewed_new = [], [], set(), set()
    for result in focused_results or []:
        proposal = result.get('review_proposal') or {}
        did = result.get('dispute_id')
        target = targets.get(did)
        if not target and proposal.get('dispute',{}).get('check') in CHECK_AXES:
            check=proposal['dispute']['check'];claim=target_for(candidate,check)
            expected='dispute_'+sha256_json([base_proposal['proposal_digest'],claim['claim_id']])[:24]
            if did==expected and proposal['dispute'].get('claim')==claim:
                target={'dispute_id':did,'claim':claim,'check':check}
        reason = None
        if not target or not same_candidate or not same_source or not same_release:
            reason = 'candidate, source, release or exact original dispute changed'
        elif (sha256_json({k: v for k, v in proposal.items() if k != 'proposal_digest'}) != proposal.get('proposal_digest')
                or proposal.get('source_proposal_digest') != base_proposal['proposal_digest']
                or proposal.get('candidate_hash') != sha256_json(candidate)
                or proposal.get('candidate_id') != candidate['candidate_id']
                or proposal.get('candidate_revision') != candidate['revision']
                or proposal.get('dispute', {}).get('claim') != target['claim']
                or proposal.get('committee_members') != base_proposal.get('committee_members')
                or (committee.weighting_policy and proposal.get('committee_policy') != base_proposal.get('committee_policy'))
                or proposal.get('resolution_policy') != VERSION):
            reason = 'focused binding or dependency-review policy mismatch'
        elif target['check'] == 'mapping' and formal_context_hash(proposal.get('ontology_context')) != formal_context_hash(ontology_context):
            reason = 'mapping definitions changed after focused review'
        if not reason:
            scoped = {e['evidence_id']: e for e in proposal.get('evidence_bundle', {}).get('items', [])}
            if not set(old) <= set(scoped) or not set(scoped) <= set(current):
                reason = 'focused evidence is no longer the protected current packet'
            elif any(any(scoped[eid].get(k) != current[eid].get(k) for k in material_keys) for eid in scoped):
                reason = 'focused source content changed'
        status = None
        if not reason:
            decision = focused_consensus(result.get('votes', []), proposal)
            status = decision['status']
            if status not in {'resolved_supports', 'resolved_opposes'}:
                reason = 'focused committee is incomplete or disagrees'
        if reason:
            rejected.append({'dispute_id': did, 'reason': reason})
            continue
        check = target['check']
        votes = result['votes']
        other = {c for v in votes for c in v.get('other_affected_checks', [])}
        affected.update(other)
        reviewed_new.update(set(scoped) - set(old))
        rid = 'resolution_' + sha256_json([proposal['proposal_digest'], votes])[:32]
        accepted.append({'resolution_id': rid, 'dispute_id': did, 'claim_id': target['claim']['claim_id'],
            'check': check, 'status': status, 'evidence_ids': sorted({eid for v in votes for eid in v['reviewed_evidence_ids']}),
            'supporting_evidence_ids':sorted({eid for v in votes for eid in v['positive_support']['evidence_ids']}),
            'counter_evidence_ids':sorted({eid for v in votes for eid in v['counterevidence_search']['evidence_ids']}),
            'other_affected_checks': sorted(other), 'proposal_digest': proposal['proposal_digest']})
        if decision.get('weight_snapshot'):
            accepted[-1]['weight_snapshot'] = deepcopy(decision['weight_snapshot'])
        targets[did]=target
        # These are ephemeral check observations. They are never serialized as replacement votes.
        for voter_id, observation in raw.items():
            if check not in observation.get('dimensions', {}):
                continue
            supports = status == 'resolved_supports'
            if committee.weighting_policy:
                focused = next((v for v in votes if v.get('voter_id')==voter_id and voter_id in decision['eligible_voter_ids']), None)
                if not focused:continue
                if focused['answer'] not in {'supports','opposes'}:
                    observation['dimensions'][check]['status']='unknown'
                    continue
                supports = focused['answer']=='supports'
                observation['dimensions'][check]['evidence_ids']=list(focused['reviewed_evidence_ids'])
            observation['dimensions'][check]['status'] = (
                'supported' if supports else 'contradicted') if check == 'triple_support' else (
                'matched' if supports else 'candidate_mapping_error') if check == 'mapping' else (
                'pass' if supports else 'fail')
            if check in {'entity_reference', 'relation_semantics', 'relation_direction', 'qualifiers'}:
                observation['expression_findings'] = [f for f in observation.get('expression_findings', []) if f['check'] != check]
    # A result that reports spillover does not certify the newly affected checks.
    for check in affected:
        if any(a['check'] == check for a in accepted):
            continue
        for observation in raw.values():
            if check in observation.get('dimensions', {}):
                observation['dimensions'][check]['status'] = 'unknown'
    complete = eligible == set(committee.voter_ids)
    axes_by_seat = {v: vote_axes(r) for v, r in raw.items()}
    for axis, dimension in dimensions.items():
        if axis == 'scope':
            continue
        if not any(axis in CHECK_AXES[a['check']] for a in accepted) and not any(axis in CHECK_AXES[c] for c in affected):
            continue
        states = {a[axis]['status'] for a in axes_by_seat.values()}
        status = next(iter(states)) if complete and len(states) == 1 else 'unresolved'
        if axis == 'mapping' and (base_proposal.get('ontology_check', {}).get('status') != 'pass'
                or base_proposal.get('mapping_preflight', {}).get('judgment_ready') is False
                or not candidate.get('triple', {}).get('relation_id')):
            status = 'context_incomplete'
        dimension.update(status=status, observed_result=status, has_conflict=len(states) > 1,
            judgment_complete=complete and status not in {'unresolved', 'unclear', 'context_incomplete'},
            resolution_ids=[a['resolution_id'] for a in accepted if axis in CHECK_AXES[a['check']]])
    pending = [{'kind': 'new_evidence_not_independently_reviewed', 'evidence_ids': sorted(set(current) - set(old) - reviewed_new)}]
    pending = [p for p in pending if p['evidence_ids']]
    formal_changed=(formal_context_hash(ontology_context)!=formal_context_hash(base_proposal.get('ontology_context')))
    if (formal_changed and
            not any(a['check']=='mapping' for a in accepted)):
        pending.append({'kind':'formal_context_changed_requires_mapping_review'})
        dimensions['mapping'].update(status='context_incomplete',judgment_complete=False)
    if not same_source or not same_candidate or not same_release:
        pending.append({'kind': 'snapshot_dependencies_changed'})
    pending += [{'kind': 'affected_check_requires_review', 'check': c} for c in sorted(affected)
                if not any(a['check'] == c for a in accepted)]
    dependency_disputes=[]
    checks=affected|({'mapping'} if formal_changed else set())
    checks|={i.get('target',{}).get('check') for i in issue_ledger if i.get('status','open')!='resolved'
             and i.get('code')=='EVIDENCE_INSUFFICIENT' and i.get('target',{}).get('check') in CHECK_AXES}
    for check in sorted(checks):
        if any(a['check']==check for a in accepted):continue
        claim=target_for(candidate,check)
        did='dispute_'+sha256_json([base_proposal['proposal_digest'],claim['claim_id']])[:24]
        dependency_disputes.append({'dispute_id':did,'claim':claim,'check':check,'question':claim['question'],
            'issue_type':TYPE_FOR[check],'opinions':[],'status':'human_review' if check=='knowledge_scope' else 'open',
            'route':'supplement_mapping_information' if check=='mapping' else 'human_scope_review' if check=='knowledge_scope' else 'supplement_source_context',
            'required_material':'new evidence or formal context affecting this check','dependency_generated':True})
    unresolved = [i for i in issue_ledger if i.get('status', 'open') != 'resolved']
    weighted_report = None
    if committee.weighting_policy:
        from .review_axes import axis_opinion
        from .vote_weighting import weight_report, annotate_disputes
        effective_report = deepcopy(report)
        for axis, dimension in effective_report['dimension_results'].items():
            for observation in dimension['observations']:
                if observation['voter_id'] in raw and observation['usable']:
                    observation['opinion']=axis_opinion(raw[observation['voter_id']],axis)
        resolved_counters = {eid for a in accepted if a['check']=='counterevidence' and a['status']=='resolved_supports'
                             for eid in a['evidence_ids']}
        weighted_report=weight_report(effective_report,effective_votes,base_proposal,committee,
            raw_overrides=raw,evidence_bundle=bundle,resolved_counter_ids=resolved_counters)
        dimensions=weighted_report['dimension_results']
        for axis, dimension in dimensions.items():
            dimension['resolution_ids']=[a['resolution_id'] for a in accepted if axis in CHECK_AXES[a['check']]]
        annotate_disputes(disputes,weighted_report)
        complete=all(d['judgment_complete'] for d in dimensions.values())
    result = {'version': VERSION, 'candidate_hash': sha256_json(candidate), 'candidate_revision': candidate['revision'],
        'base_proposal_digest': base_proposal['proposal_digest'], 'original_votes_hash': original_hash,
        'evidence_hash': bundle.get('evidence_hash'), 'ontology_context_hash': sha256_json(ontology_context),
        'issue_ledger_hash': sha256_json(issue_ledger), 'dimension_results': dimensions,
        'whole_vote_eligible_voter_ids': report['whole_vote_eligible_voter_ids'],
        'effective_distinct_families':len({v.get('model_family') for v in effective_votes if v.get('voter_id') in eligible}),
        'effective_mapping_bindings':{v['voter_id']:(v.get('dimensions') or {}).get('mapping',{}).get('relation_id')
                                      for v in effective_votes if v.get('voter_id') in eligible},
        'effective_supporting_evidence_ids':sorted({e for v in effective_votes if v.get('voter_id') in eligible and v.get('source_support')=='supported'
                                                  for e in v.get('supporting_evidence_ids',[])}|
                                                 {e for r in accepted if r['status']=='resolved_supports' for e in r['supporting_evidence_ids']}),
        'accepted_semantic_review_ids':semantic_ids,
        'accepted_original_receipt_ids':sorted(set(receipt_ids)),
        'accepted_resolutions': accepted, 'rejected_resolutions': rejected, 'new_obligations': pending,
        'dependency_disputes':dependency_disputes,
        'current_disputes':deepcopy(disputes['disputes']),
        'expression_findings':[dict(deepcopy(f),voter_id=v) for v,r in raw.items() for f in r.get('expression_findings',[])],
        'unresolved_issue_ids': [i['issue_id'] for i in unresolved if i.get('critical')],
        'unresolved_dispute_ids': [did for did in targets if not any(a['dispute_id'] == did for a in accepted)
            and (not committee.weighting_policy or next((d.get('status') for d in disputes['disputes'] if d['dispute_id']==did),None) not in {'nonblocking_abstention','policy_equivalent'})],
        'retained_counter_evidence_ids': sorted({e for v in [*original_votes,*effective_votes] for e in v.get('counter_evidence_ids', [])}|
                                              {e for r in accepted for e in r['counter_evidence_ids']}),
        'original_votes_changed': False, 'whole_candidate_admitted': False}
    result['semantic_obligations_complete'] = complete and not pending and not result['unresolved_issue_ids'] and not result['unresolved_dispute_ids']
    if weighted_report:
        result['weight_snapshot']=weighted_report['weight_snapshot']
    if any(a['check']=='mapping' and a['status']=='resolved_supports' for a in accepted) and dimensions['mapping']['status']=='valid':
        result['effective_mapping_bindings']={v:candidate['triple']['relation_id'] for v in eligible}
    result['snapshot_id'] = 'snapshot_' + sha256_json(result)
    assert sha256_json(original_votes) == original_hash
    return result


def from_context(proposal, votes, committee, context):
    if not context.get('resolution_snapshots_enabled'):
        return None
    return resolve_review_state(proposal, votes, context.get('focused_review_results', []),
        context.get('issue_history', []), committee, candidate=context.get('resolution_candidate') or proposal['candidate'],
        bundle=context.get('resolution_bundle') or proposal['evidence_bundle'],
        ontology_context=context.get('resolution_ontology_context', proposal.get('ontology_context')),
        semantic_results=list((context.get('semantic_recheck_results') or {}).values()),
        original_receipts=context.get('original_receipt_results',[]))


def apply_consensus(consensus, snapshot):
    result = deepcopy(consensus)
    if not snapshot:
        return result
    if snapshot.get('weight_snapshot'):
        from .vote_weighting import apply_to_consensus
        result=apply_to_consensus(result, snapshot, {}, admission_blocked=not snapshot['semantic_obligations_complete'])
    dimensions = snapshot['dimension_results']
    result['resolution_snapshot'] = deepcopy(snapshot)
    result['resolution_snapshot_id'] = snapshot['snapshot_id']
    result['eligible_voter_ids']=snapshot['whole_vote_eligible_voter_ids']
    result['distinct_family_count']=snapshot['effective_distinct_families']
    if not snapshot.get('weight_snapshot'):
        result['diversity_ready']=snapshot['effective_distinct_families']>=result.get('minimum_distinct_families',3)
    for axis, dimension in dimensions.items():
        result.setdefault('review_axes', {}).setdefault(axis, {}).update(status=dimension['status'])
    expression=result['review_axes']['expression']
    resolved={r['check'] for r in snapshot['accepted_resolutions']}
    original_findings=expression.get('historical_findings',expression.get('findings',[]))
    expression['historical_findings']=deepcopy(original_findings)
    expression['findings']=deepcopy(snapshot['expression_findings'])
    expression['focused_findings']=[deepcopy(r) for r in snapshot['accepted_resolutions'] if r['check'] in
        {'entity_reference','relation_semantics','relation_direction','qualifiers'}]
    result['review_axes']['source']['evidence_ids']=sorted(set(result['review_axes']['source'].get('evidence_ids',[]))|
        set(snapshot.get('effective_supporting_evidence_ids',[]))|set(snapshot.get('retained_counter_evidence_ids',[])))
    result['source_support_consensus'] = {**result.get('source_support_consensus', {}), 'result': dimensions['source']['status'],
                                          'derivation': 'version_bound_scoped_resolution'}
    result['expression_status'] = {'standard': 'faithful', 'semantic_revision': 'needs_revision'}.get(dimensions['expression']['status'], 'unresolved')
    result['effective_mapping_status']={'valid':'matched','conflict':'constraint_conflict','unassigned':'unassigned'}.get(dimensions['mapping']['status'],'ambiguous')
    ready = snapshot['semantic_obligations_complete'] and all(dimensions[n]['status'] == wanted for n, wanted in
        {'source': 'supported', 'expression': 'standard', 'mapping': 'valid', 'scope': 'include'}.items())
    result['admission_consensus'] = {**result.get('admission_consensus', {}),
                                   'result': 'accept_consensus' if ready else 'unresolved'}
    return result


def update_issue_ledger(state,base,original_votes,committee,snapshot):
    """The runtime and zero-call report apply identical, exact-target issue rules."""
    from .issue_ledger import IssueLedger
    from .consensus import aggregate_votes
    ledger=IssueLedger(state)
    received=set(snapshot.get('accepted_semantic_review_ids',[]))|set(snapshot.get('accepted_original_receipt_ids',[]))
    seen=set(state.context.get('observed_resolution_response_ids',[]))
    if received-seen:
        effective=deepcopy(original_votes)
        for result in state.context.get('semantic_recheck_results',{}).values():
            if (result.get('review_proposal') or {}).get('task_id') not in received:continue
            vote=result['review_vote'];effective=[vote if v['voter_id']==vote['voter_id'] else v for v in effective]
        for vote in state.context.get('original_receipt_results',[]):
            if vote.get('attempt_id') in received:effective=[vote if v['voter_id']==vote['voter_id'] else v for v in effective]
        ledger.observe(base,effective,aggregate_votes(effective,committee,proposal=base,proposal_digest=base['proposal_digest']),committee.required_votes)
        state.context['observed_resolution_response_ids']=sorted(seen|received)
    resolutions={r['dispute_id']:r for r in snapshot['accepted_resolutions']}
    for dispute in [*snapshot.get('current_disputes',[]),*snapshot.get('dependency_disputes',[])]:
        if dispute.get('status') in {'nonblocking_abstention','policy_equivalent'}:continue
        state.context.setdefault('dispute_baseline',{'proposal_digest':base['proposal_digest'],'round_id':base['round_id']})
        report=state.context.setdefault('dispute_report',{'disputes':[]})
        if dispute['dispute_id'] not in {d['dispute_id'] for d in report['disputes']}:report['disputes'].append(deepcopy(dispute))
        if dispute['dispute_id'] not in resolutions:
            ledger.open('claim_dispute',dispute['question'],True,proposal=base['proposal_digest'],
                evidence_ids=[e['evidence_id'] for e in state.bundle.get('items',[])],
                target={'claim_id':dispute['claim']['claim_id'],'check':dispute['check']},dimensions=CHECK_AXES[dispute['check']])
    for resolution in snapshot['accepted_resolutions']:
        state.protected_ids=sorted(set(state.protected_ids)|set(resolution.get('supporting_evidence_ids',[]))|set(resolution.get('counter_evidence_ids',[])))
        if resolution['status']!='resolved_supports':continue
        for issue in list(ledger.unresolved()):
            if issue.get('target',{}).get('claim_id')==resolution['claim_id']:
                ledger.resolve_target(issue['issue_id'],CHECK_AXES[resolution['check']],
                    resolution_id=resolution['resolution_id'],proposal=base,evidence_ids=resolution['evidence_ids'])


def display_projection(decision,diagnostic,context):
    from .review_axes import display_axes,route_axes
    axes=display_axes(decision);pending=(decision or {}).get('pending_actions',[])
    snapshot=context.get('resolution_snapshot')
    if snapshot and diagnostic:
        axes=apply_consensus({'review_axes':axes},snapshot)['review_axes']
        coverage_key='judgment_complete' if snapshot.get('weight_snapshot') else 'seat_coverage_complete'
        pending=route_axes(axes,complete=all(d[coverage_key] for d in diagnostic['dimension_results'].values()))['pending_actions']
        if context.get('technical_recovery_tasks'):pending=[*pending,'technical_recovery']
    return axes,pending
