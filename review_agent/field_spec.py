"""Versioned field meanings and bounded, source-bound derived normalizations."""
from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from .contracts import sha256_json

VERSION = 'review_fields_v1'
REPORT_VERSION = 'field_standardization_v1'


@lru_cache(maxsize=1)
def _spec():
    result=json.loads((Path(__file__).resolve().parents[1]/'specs/review_fields_v1.json').read_text(encoding='utf-8'))
    if result['version'] != VERSION: raise ValueError('field specification version mismatch')
    return result


def specification():
    return deepcopy(_spec())


def _source_ids(bundle, value):
    return sorted(e['evidence_id'] for e in bundle.get('items', [])
                  if e.get('anchor_valid') and value in str(e.get('quote') or ''))


def _uncertain_attachment(original,value):
    matching=[a for a in (original.get('semantic_context') or {}).get('qualifier_attachments', []) if a.get('value')==value]
    attachments={(a.get('attachment'),a.get('scope_id')) for a in matching}
    return (len(attachments)>1 or any(a.get('attachment') not in {
        'relation','triple','proposition','surface_relation','whole_triple'} for a in matching))


def prepare(candidate, bundle, spec):
    if spec != specification(): raise ValueError('unsupported field specification')
    original=deepcopy(candidate['triple']); normalized=deepcopy(original)
    qualifiers=normalized.setdefault('qualifiers', {}); states=normalized.setdefault('qualifier_status', {})
    qualifiers.setdefault('frequency', None); states.setdefault('frequency', 'not_stated')
    tokens={v.casefold() for v in spec['deterministic_frequency_tokens']}
    basis={'candidate_hash':sha256_json(candidate),'evidence_hash':bundle['evidence_hash'],'spec_hash':sha256_json(spec)}
    plans=[]; reviews=[]

    def add(rule, operation, source_field, value, steps):
        item={'rule_id':rule,'operation':operation,'source_field':source_field,'value':value,
              'steps':steps,'evidence_ids':_source_ids(bundle,value),
              'attachment_preserved_from':deepcopy((original.get('semantic_context') or {}).get('qualifier_attachments', []))}
        item['plan_id']=sha256_json({**basis,**item})
        plans.append(item)

    for field in ('applicability','quantifier'):
        value=qualifiers.get(field)
        if not isinstance(value,str) or value.casefold() not in tokens: continue
        if (states.get(field)!='extracted' or not _source_ids(bundle,value) or _uncertain_attachment(original,value) or
                qualifiers.get('frequency') not in (None,value) or states.get('frequency') not in {'not_stated','extracted'}):
            reviews.append({'rule_id':'FREQ-001','field':field,'reason':'频率来源、目标冲突或语义附着仍需核验，未执行迁移'})
            continue
        add('FREQ-001','move_frequency',field,value,[
            {'path':'qualifiers.'+field,'before':value,'after':None},
            {'path':'qualifiers.frequency','before':qualifiers['frequency'],'after':value}])
        qualifiers[field]=None;states[field]='not_stated';qualifiers['frequency']=value;states['frequency']='extracted'
    frequency=qualifiers.get('frequency'); relation=normalized.get('surface_relation') or ''
    if (isinstance(frequency,str) and frequency.casefold() in tokens and states.get('frequency')=='extracted'
            and _source_ids(bundle,frequency) and not _uncertain_attachment(original,frequency)):
        # Remove one exact duplicate only; no translation, paraphrase or relation substitution.
        if relation.startswith(frequency): remainder=relation[len(frequency):]
        elif relation.endswith(frequency): remainder=relation[:-len(frequency)]
        else: remainder=None
        if remainder is not None and remainder.strip():
            add('DUP-001','remove_duplicate_frequency','surface_relation',frequency,
                [{'path':'surface_relation','before':relation,'after':remainder}])
            normalized['surface_relation']=remainder
    if str((original.get('qualifiers') or {}).get('quantifier') or '').casefold() in {'分别','respectively'}:
        reviews.append({'rule_id':'PAIR-001','field':'quantifier','reason':'需要明确对象配对；不按数量或顺序自动拆分'})
    if (original.get('qualifiers') or {}).get('modality') in {'can','may','possible'}:
        reviews.append({'rule_id':'MOD-001','field':'modality','reason':'结合完整句子和公式区分定义、等价表示与能力或可能性；不自动更改模态'})
    return {'version':REPORT_VERSION,'spec_version':spec['version'],**basis,
        'candidate_id':candidate['candidate_id'],'revision':candidate['revision'],
        'original_expression':original,'normalized_expression':normalized,'plans':plans,'semantic_review':reviews,
        'original_candidate_changed':False,'semantic_preservation_confirmed':False,'admission_authorized':False}


def frozen(proposal):
    from .contracts import proposal_digest
    spec=proposal.get('field_spec'); report=proposal.get('field_standardization')
    if (spec != specification() or proposal.get('field_spec_version')!=VERSION or sha256_json(spec)!=proposal.get('field_spec_hash') or
            not isinstance(report,dict) or sha256_json(report)!=proposal.get('field_standardization_hash') or
            report!=prepare(proposal['candidate'],proposal['evidence_bundle'],spec) or
            proposal_digest(proposal)!=proposal.get('proposal_digest')):
        raise ValueError('frozen field specification or normalization binding mismatch')
    return deepcopy(report)


def finding_errors(raw, proposal):
    report=frozen(proposal);plans={p['plan_id']:p for p in report['plans']}
    evidence={e['evidence_id']:e for e in proposal['evidence_bundle']['items']}
    errors=[]; acknowledged=set()
    for finding in raw['expression_findings']:
        if set(finding['evidence_ids'])-evidence.keys():errors.append('field findings reference foreign evidence')
        if not any(evidence.get(eid,{}).get('anchor_valid') for eid in finding['evidence_ids']):
            errors.append('field findings require anchored source evidence')
        plan=finding.get('normalization')
        if plan is None:continue
        bound=plans.get(plan['plan_id'])
        if (not bound or finding['rule_id']!=bound['rule_id'] or not set(bound['evidence_ids']) & set(finding['evidence_ids'])):
            errors.append('normalization must reference the exact source-bound deterministic plan')
        else:acknowledged.add(bound['plan_id'])
    # A judge must explicitly assess proposed migrations; silence does not approve a normalized view.
    for plan in report['plans']:
        if plan['plan_id'] not in acknowledged and not any(f['rule_id']==plan['rule_id'] and f['kind'] in {'semantic_error','unresolved'} for f in raw['expression_findings']):
            errors.append('each deterministic field plan requires explicit preservation or semantic concern')
    return errors


def diagnostic_report(entry, proposal):
    from .mapping_preflight import diagnostic_report as bound_diagnostic
    report=bound_diagnostic(entry,proposal)
    if (report and report.get('spec_hash')==sha256_json(specification()) and report.get('version')==REPORT_VERSION
            and entry['report']==prepare(proposal['candidate'],proposal['evidence_bundle'],specification())):
        return report
    return None
