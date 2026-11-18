"""Frozen, replayable per-axis weighting. Scores are support mass, not probabilities.

The first feature policy measures recorded check completion only. It makes no claim
to automatically establish semantic entailment; that remains an independent review.
"""
from copy import deepcopy
import math

from .contracts import sha256_json

VERSION = 'axis_weighting_v1'
FEATURE_VERSION = 'check_completion_v1'
AXES = ('source', 'expression', 'mapping', 'scope')
DECISIVE = {'source': {'supported', 'contradicted'},
            'expression': {'standard', 'lossless_normalization', 'semantic_revision'},
            'mapping': {'valid', 'conflict'}, 'scope': {'include', 'exclude'}}
UNKNOWN = {'unknown', 'unresolved', 'unassigned', 'context_incomplete', 'insufficient'}


def _number(value, name, low=0, high=1):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('invalid weighting ' + name)
    return float(value)


def normalize_policy(value, size=3):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('vote_weighting must be an object')
    allowed = {'mode', 'version', 'feature_version', 'thresholds', 'min_available_mass',
               'min_opinions', 'min_families', 'max_seat_weight', 'adaptation_floor',
               'prior', 'prior_strength', 'min_history_samples', 'reliability'}
    if set(value) - allowed:
        raise ValueError('unknown vote_weighting fields: ' + ', '.join(sorted(set(value) - allowed)))
    mode = value.get('mode', 'legacy')
    if mode == 'legacy':
        if set(value) - {'mode'}:
            raise ValueError('legacy weighting accepts only mode')
        return None
    if mode not in {'equal', 'static', 'dynamic'}:
        raise ValueError('unsupported vote_weighting mode')
    if size < 2:
        raise ValueError('weighted review requires at least two seats')
    p = {'mode': mode, 'version': VERSION, 'feature_version': FEATURE_VERSION,
         'thresholds': dict.fromkeys(AXES, .70), 'min_available_mass': .65,
         'min_opinions': 2, 'min_families': 2, 'max_seat_weight': .49,
         'adaptation_floor': .25, 'prior': .5, 'prior_strength': 4.0,
         'min_history_samples': 8,
         'reliability': {'data_version': 'cold_start', 'partition': 'development', 'models': {}}}
    p.update(deepcopy(value))
    if p['version'] != VERSION or p['feature_version'] != FEATURE_VERSION:
        raise ValueError('unsupported weighting/feature version')
    for key in ('min_opinions', 'min_families', 'min_history_samples'):
        if type(p[key]) is not int or p[key] < (0 if key == 'min_history_samples' else 2):
            raise ValueError('invalid weighting ' + key)
    if not p['min_families'] <= p['min_opinions'] <= size:
        raise ValueError('weighting quorum exceeds committee size')
    for key in ('min_available_mass', 'max_seat_weight', 'adaptation_floor', 'prior'):
        p[key] = _number(p[key], key)
    p['prior_strength'] = _number(p['prior_strength'], 'prior_strength', .000001, 1000000)
    if not 1 / size <= p['max_seat_weight'] < .5 or not 0 < p['adaptation_floor'] <= 1 or not 0 < p['prior'] < 1:
        raise ValueError('infeasible seat cap or zero prior/adaptation')
    if p['min_available_mass'] <= p['max_seat_weight']:
        raise ValueError('one seat cannot satisfy available-mass quorum')
    if not isinstance(p['thresholds'], dict) or set(p['thresholds']) != set(AXES):
        raise ValueError('all four weighting thresholds must be explicit')
    p['thresholds'] = {a: _number(p['thresholds'][a], a + ' threshold') for a in AXES}
    if any(t <= p['max_seat_weight'] for t in p['thresholds'].values()):
        raise ValueError('one seat cannot satisfy a decision threshold')
    history = p['reliability']
    if (not isinstance(history, dict) or set(history) != {'data_version', 'partition', 'models'}
            or not isinstance(history['data_version'], str) or not history['data_version'].strip()
            or history['partition'] not in {'train', 'development'} or not isinstance(history['models'], dict)):
        raise ValueError('reliability requires versioned train/development statistics, never test labels')
    for voter, record in history['models'].items():
        if (not isinstance(voter, str) or not isinstance(record, dict)
                or set(record)!={'model_identity','model_family','axes'}
                or any(not isinstance(record[k],str) or not record[k].strip() for k in ('model_identity','model_family'))
                or not isinstance(record['axes'],dict) or set(record['axes'])-set(AXES)):
            raise ValueError('invalid reliability model/axis')
        for stats in record['axes'].values():
            if (not isinstance(stats, dict) or set(stats) != {'correct', 'evaluated'}
                    or any(type(stats[k]) is not int for k in stats)
                    or not 0 <= stats['correct'] <= stats['evaluated']):
                raise ValueError('invalid reliability counts')
    return p


def configured_policy(config):
    consensus = config.get('consensus') or {}
    policy = normalize_policy(config.get('vote_weighting'), int(consensus.get('committee_size', 4)))
    if consensus.get('weighted_quorum_enabled') and policy is None:
        raise ValueError('weighted_quorum_enabled requires an explicit supported vote_weighting mode')
    if policy and config.get('validator_contract_version') not in {'validator_response_v7', 'validator_response_v8'}:
        raise ValueError('weighted review requires a four-axis validator contract')
    return policy


def committee_policy(committee):
    result = {'committee_size': len(committee.voter_ids), 'required_votes': committee.required_votes,
              'minimum_distinct_families': committee.minimum_distinct_families}
    if committee.weighting_policy is not None:
        result['vote_weighting'] = deepcopy(committee.weighting_policy)
    return result


def policy_from_config(config):
    c = config.get('consensus') or {}
    result = {key: c.get(key, default) for key, default in
              (('committee_size', 4), ('required_votes', 3), ('minimum_distinct_families', 3))}
    p = configured_policy(config)
    if p:
        result['vote_weighting'] = p
    return result


def frozen_policy(proposal, committee):
    from .contracts import proposal_digest
    if proposal_digest(proposal) != proposal.get('proposal_digest'):
        raise ValueError('weighting requires an intact frozen proposal digest')
    if proposal.get('candidate_review_hash') and proposal['candidate_review_hash'] != sha256_json(proposal['candidate']):
        raise ValueError('candidate changed after proposal freeze')
    expected = committee_policy(committee)
    if proposal.get('committee_policy_hash') != sha256_json(expected) or proposal.get('committee_policy') != expected:
        raise ValueError('weighting policy differs from frozen proposal')
    return expected['vote_weighting']


def capped_weights(values, cap):
    """Proportional water filling onto the simplex with a fixed upper bound."""
    if not values or cap * len(values) < 1 - 1e-12:
        raise ValueError('infeasible weighting cap')
    if any(not math.isfinite(v) or v < 0 for v in values.values()) or sum(values.values()) <= 0:
        raise ValueError('nonfinite, negative or all-zero weighting factors')
    weights, active, remaining = {}, list(values), 1.0
    while active:
        total = sum(values[k] for k in active)
        trial = {k: remaining * values[k] / total if total else remaining / len(active) for k in active}
        capped = [k for k in active if trial[k] > cap + 1e-12]
        if not capped:
            weights.update(trial)
            break
        for k in capped:
            weights[k] = cap
            remaining -= cap
            active.remove(k)
    return {k: weights[k] for k in values}


def _reliability(p, voter, axis, member):
    record = p['reliability']['models'].get(voter, {})
    stats = record.get('axes',{}).get(axis)
    identity=str(member.get('model_identity') or member.get('model_id') or '').strip().casefold()
    if record and (record['model_identity'].strip().casefold()!=identity or record['model_family']!=member.get('model_family')):
        return p['prior'], {'reason':'historical_model_identity_mismatch','counts':deepcopy(stats)}
    if not stats or stats['evaluated'] < p['min_history_samples']:
        return p['prior'], {'reason': 'insufficient_history', 'counts': deepcopy(stats)}
    r = (stats['correct'] + p['prior'] * p['prior_strength']) / (stats['evaluated'] + p['prior_strength'])
    return r, {'reason': 'smoothed_development_accuracy', 'counts': deepcopy(stats)}


def completion_features(vote, report, axis, raw=None):
    from .review_observability import DEPENDENCIES
    from .validator_contracts import raw_from_vote
    if not report or not report.get('global_trustworthy'):
        return {'available': False, 'known_fraction': None, 'reason': 'missing_or_untrusted_opinion'}
    if raw is None:
        try:
            raw = raw_from_vote(vote)
        except (ValueError, KeyError, TypeError):
            return {'available': False, 'known_fraction': None, 'reason': 'dimensions_unavailable'}
    dimensions = raw.get('dimensions') or {}
    names = sorted(DEPENDENCIES[axis])
    known = [n for n in names if dimensions.get(n, {}).get('status') not in UNKNOWN | {None}
             and dimensions.get(n, {}).get('evidence_ids')]
    return {'available': True, 'known_fraction': len(known) / len(names),
            'known_checks': known, 'required_checks': names, 'meaning': 'recorded_completion_not_entailment'}


def _label(observation, axis, proposal):
    if not observation.get('usable'):
        return None
    opinion = observation.get('opinion') or {}
    if axis == 'scope':
        from .scope_policy import classification_decision
        return classification_decision(opinion.get('knowledge_scope'), True, proposal.get('review_scope_policy'))['status']
    return opinion.get('status')


def weight_report(report, votes, proposal, committee, *, raw_overrides=None, evidence_bundle=None, resolved_counter_ids=()):
    """Replace diagnostic unanimity with the same frozen policy used for decisions."""
    p = frozen_policy(proposal, committee)
    if set(p['reliability']['models']) - set(committee.voter_ids):
        raise ValueError('reliability statistics contain an unknown committee seat')
    selected = {}
    for v in votes:
        if v.get('voter_id') in committee.voter_ids:
            selected.setdefault(v['voter_id'], v)
    out = deepcopy(report)
    bundle = evidence_bundle or proposal['evidence_bundle']
    if sha256_json({k:v for k,v in bundle.items() if k!='evidence_hash'}) != bundle.get('evidence_hash'):
        raise ValueError('evidence changed after packet freeze')
    available_ids = {e['evidence_id'] for e in bundle.get('items', []) if e.get('anchor_valid')}
    counters = sorted({eid for voter, v in selected.items()
                       if report['vote_validation'].get(voter, {}).get('global_trustworthy')
                       for eid in v.get('counter_evidence_ids', []) if eid in available_ids} - set(resolved_counter_ids))
    snapshot = {'version': VERSION, 'mode': p['mode'], 'policy_hash': sha256_json(p),
                'reliability_hash': sha256_json(p['reliability']), 'feature_version': FEATURE_VERSION,
                'candidate_hash': sha256_json(proposal['candidate']), 'proposal_digest': proposal['proposal_digest'],
                'evidence_hash': bundle.get('evidence_hash'), 'ontology_context_hash': proposal.get('ontology_context_hash'),
                'committee_members_hash': sha256_json(proposal['committee_members']),
                'original_votes_hash': sha256_json(votes), 'critical_counter_evidence_ids': counters, 'axes': {}}
    snapshot['effective_observations_hash'] = sha256_json(raw_overrides or {})
    snapshot['resolved_counter_evidence_ids'] = sorted(set(resolved_counter_ids))
    for axis in AXES:
        dimension = out['dimension_results'][axis]
        observations = {o['voter_id']: o for o in dimension['observations']}
        seats, factors = {}, {}
        for voter in committee.voter_ids:
            v = selected.get(voter, {})
            member=next((m for m in proposal['committee_members'] if m['id']==voter),{})
            r, history = _reliability(p, voter, axis, member)
            features = completion_features(v, report['vote_validation'].get(voter), axis,
                                          (raw_overrides or {}).get(voter))
            g = p['adaptation_floor'] + (1 - p['adaptation_floor']) * features['known_fraction'] if features['available'] else 1.0
            label = _label(observations.get(voter, {}), axis, proposal)
            usable = label in DECISIVE[axis]
            if axis == 'mapping' and label == 'valid':
                raw = (raw_overrides or {}).get(voter) or v
                usable = (raw.get('dimensions') or {}).get('mapping', {}).get('relation_id') == proposal['candidate']['triple'].get('relation_id')
            factors[voter] = 1.0 if p['mode'] == 'equal' else r * (g if p['mode'] == 'dynamic' else 1.0)
            seats[voter] = {'model_id': member.get('model_id'), 'model_family': member.get('model_family'),
                            'reliability': r, 'history': history, 'adaptation': g,
                            'features': features, 'opinion': label, 'usable': usable,
                            'missing_reasons': observations.get(voter, {}).get('missing_reasons', ['seat_missing'])}
        weights = capped_weights(factors, p['max_seat_weight'])
        scores = dict.fromkeys(sorted(DECISIVE[axis]), 0.0)
        for voter, seat in seats.items():
            seat.update(weight=weights[voter], effective_weight=weights[voter] if seat['usable'] else 0.0)
            if seat['usable']:
                scores[seat['opinion']] += weights[voter]
        mass = sum(s['effective_weight'] for s in seats.values())
        labels = {s['opinion'] for s in seats.values() if s['usable']}
        winners = []
        for label, score in scores.items():
            supporting = [s for s in seats.values() if s['usable'] and s['opinion'] == label]
            families = {s['model_family'] for s in supporting} - {None, ''}
            if (score + 1e-12 >= p['thresholds'][axis] and mass + 1e-12 >= p['min_available_mass']
                    and len(supporting) >= p['min_opinions'] and len(families) >= p['min_families']):
                winners.append(label)
        status = winners[0] if len(winners) == 1 else 'unclear' if axis == 'scope' else 'unresolved'
        # All usable opposing substantive judgments remain obligations, regardless of weight.
        conflict = len(labels) > 1 or (axis == 'source' and bool(counters) and status != 'contradicted')
        if conflict:
            status = 'unclear' if axis == 'scope' else 'unresolved'
        if axis == 'mapping' and status != 'conflict' and (proposal.get('ontology_check', {}).get('status') != 'pass'
                or proposal.get('mapping_preflight', {}).get('judgment_ready') is False):
            status = 'context_incomplete'
        if axis == 'source':
            from .revision_patch import expression_diagnostics
            if status == 'supported' and not expression_diagnostics(proposal['candidate']).get('expression_complete', True):
                status = 'insufficient'
        complete = status in DECISIVE[axis]
        dimension.update(status=status, observed_result=status, judgment_complete=complete,
                         has_conflict=conflict, required_seats=p['min_opinions'],
                         weighted_quorum_complete=complete, available_mass=mass)
        snapshot['axes'][axis] = {'seats': seats, 'scores': scores, 'available_mass': mass,
                                  'valid_opinion_count': sum(s['usable'] for s in seats.values()),
                                  'threshold': p['thresholds'][axis], 'status': status, 'has_conflict': conflict}
    snapshot['snapshot_id'] = 'weights_' + sha256_json(snapshot)
    out['weight_snapshot'] = snapshot
    return out


def quorum_met(voter_ids, axis, snapshot, policy):
    seats = snapshot['axes'][axis]['seats']
    usable = [seats[v] for v in set(voter_ids) if v in seats and seats[v]['usable']]
    return (len(usable) >= policy['min_opinions']
            and len({s['model_family'] for s in usable} - {None, ''}) >= policy['min_families']
            and sum(s['effective_weight'] for s in usable) + 1e-12 >= policy['thresholds'][axis]
            and snapshot['axes'][axis]['available_mass'] + 1e-12 >= policy['min_available_mass'])


def annotate_disputes(disputes, report):
    """Only abstentions cease to be blocking; grounded disagreements remain open."""
    from .resolution_state import CHECK_AXES
    for dispute in disputes.get('disputes', []):
        axes = CHECK_AXES.get(dispute['check'], set())
        if (axes=={'scope'} and report['dimension_results']['scope']['judgment_complete']
                and not report['dimension_results']['scope']['has_conflict']):
            dispute.update(status='policy_equivalent',weighting_basis=report['weight_snapshot']['snapshot_id'])
            continue
        acceptable = {'pass', 'supported', 'not_applicable', 'unknown', 'unresolved', 'unassigned', 'context_incomplete', 'matched'}
        if (axes and dispute.get('status') in {'open', 'human_review'}
                and not dispute.get('positive_role_conflict')
                and all(report['dimension_results'][a]['judgment_complete'] and not report['dimension_results'][a]['has_conflict'] for a in axes)
                and all(o['status'] in acceptable for o in dispute.get('opinions', []))):
            dispute.update(status='nonblocking_abstention', weighting_basis=report['weight_snapshot']['snapshot_id'])


def focused_weighting(votes, proposal):
    """Aggregate already identity/contract-validated focused responses, per frozen axis."""
    from .resolution_state import CHECK_AXES
    p = normalize_policy(proposal['committee_policy']['vote_weighting'], proposal['committee_policy']['committee_size'])
    by_id = {v['voter_id']: v for v in votes}
    axes, accepted = {}, []
    for axis in sorted(CHECK_AXES[proposal['dispute']['check']]):
        factors, seats = {}, {}
        for member in proposal['committee_members']:
            voter = member['id']; v = by_id.get(voter, {})
            answer = v.get('answer'); usable = answer in {'supports', 'opposes'}
            r, history = _reliability(p, voter, axis, member)
            g = (1.0 if usable else p['adaptation_floor']) if v else 1.0
            factors[voter] = 1.0 if p['mode']=='equal' else r * (g if p['mode']=='dynamic' else 1.0)
            seats[voter] = {'model_family': member['model_family'], 'answer': answer, 'usable': usable,
                            'reliability': r, 'adaptation': g, 'history': history}
        # Unconfigured frozen slots remain in the denominator, even in focused review.
        if len(seats) != proposal['committee_policy']['committee_size']:
            return {'status':'review_incomplete', 'eligible_voter_ids':sorted(by_id), 'whole_candidate_admitted':False,
                    'original_votes_changed':False, 'majority_is_resolution':False}
        weights = capped_weights(factors, p['max_seat_weight'])
        for voter, seat in seats.items():
            seat.update(weight=weights[voter], effective_weight=weights[voter] if seat['usable'] else 0.0)
        mass = sum(s['effective_weight'] for s in seats.values())
        answers = {s['answer'] for s in seats.values() if s['usable']}
        winner = None
        for answer in ('supports','opposes'):
            support = [s for s in seats.values() if s['usable'] and s['answer']==answer]
            if (answers=={answer} and mass+1e-12>=p['min_available_mass']
                    and sum(s['effective_weight'] for s in support)+1e-12>=p['thresholds'][axis]
                    and len(support)>=p['min_opinions']
                    and len({s['model_family'] for s in support}-{None,''})>=p['min_families']):
                winner = answer
        axes[axis] = {'seats':seats,'available_mass':mass,'winner':winner,'threshold':p['thresholds'][axis]}
        accepted.append(winner)
    winner = accepted[0] if accepted and accepted[0] and len(set(accepted))==1 else None
    snapshot = {'version':VERSION,'kind':'focused_review','mode':p['mode'],'policy_hash':sha256_json(p),
                'proposal_digest':proposal['proposal_digest'],'votes_hash':sha256_json(votes),'axes':axes}
    snapshot['snapshot_id']='weights_'+sha256_json(snapshot)
    return {'status':'resolved_'+winner if winner else 'human_review' if len(by_id)==len(proposal['committee_members']) else 'review_incomplete',
            'eligible_voter_ids':sorted(by_id),'weight_snapshot':snapshot,
            'whole_candidate_admitted':False,'original_votes_changed':False,'majority_is_resolution':False}


def apply_to_consensus(consensus, report, proposal, *, admission_blocked=False):
    result = deepcopy(consensus)
    dimensions = report['dimension_results']
    snapshot = report['weight_snapshot']
    result['weight_snapshot'] = deepcopy(snapshot)
    from .review_axes import VERSION as axes_version
    result['review_axes'] = {'version':axes_version, **{a: deepcopy(d) for a, d in dimensions.items()}}
    source = dimensions['source']['status']
    result['source_support_consensus'] = {'result': source, 'derivation': VERSION,
        'support_voter_ids': [v for v, s in snapshot['axes']['source']['seats'].items() if s['usable'] and s['opinion'] == 'supported']}
    expected = {'source': 'supported', 'expression': 'standard', 'mapping': 'valid', 'scope': 'include'}
    ready = all(dimensions[a]['status'] == wanted for a, wanted in expected.items()) and not admission_blocked
    result['admission_consensus'] = {'result': 'accept_consensus' if ready else 'unresolved'}
    result['result'] = 'accept_consensus' if ready else 'reject_consensus' if source == 'contradicted' else 'unresolved'
    result['weighted_review_complete'] = all(d['judgment_complete'] for d in dimensions.values())
    result['diversity_ready'] = result['weighted_review_complete']
    result['expression_status'] = {'standard': 'faithful', 'semantic_revision': 'needs_revision',
                                   'lossless_normalization': 'normalization_required'}.get(dimensions['expression']['status'], 'unresolved')
    result['effective_mapping_status'] = {'valid': 'matched', 'conflict': 'constraint_conflict'}.get(dimensions['mapping']['status'], 'ambiguous')
    scope_seats = snapshot['axes']['scope']['seats'].values()
    scope_votes = [s for s in scope_seats if s['usable'] and s['opinion'] == dimensions['scope']['status']]
    result['scope_confirmed'] = dimensions['scope']['judgment_complete']
    # Preserve the original category for scope_route (the final axis remains authoritative).
    if scope_votes:
        winning_ids={v for v,s in snapshot['axes']['scope']['seats'].items() if s['usable'] and s['opinion']==dimensions['scope']['status']}
        result['knowledge_scope'] = next((o['opinion'].get('knowledge_scope') for o in dimensions['scope']['observations']
            if o['voter_id'] in winning_ids), 'unresolved')
    return result


def operation(report, *, blockers=(), repairable=False):
    """Research recommendation only; never grants production admission."""
    dims = report['dimension_results']
    statuses = {a: dims[a]['status'] for a in AXES}
    if statuses['scope']=='exclude' and not any(b.startswith(('execution:', 'gap:version_stale',
            'gap:validation_missing', 'gap:protected_evidence', 'gap:unreviewed_discovered', 'resolution_dependency')) for b in blockers):
        return 'discard'
    if blockers:
        return 'manual_review'
    if statuses['expression'] in {'semantic_revision', 'lossless_normalization'} or repairable:
        return 'revise'
    if statuses == {'source': 'supported', 'expression': 'standard', 'mapping': 'valid', 'scope': 'include'}:
        return 'accept'
    # A contradicted candidate is repair-first. Only an independently confirmed scope
    # exclusion can be discarded without a separate bounded repairability assessment.
    if statuses['source'] == 'contradicted':
        return 'revise'
    return 'manual_review'
