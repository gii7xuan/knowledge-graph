"""Normative field rules, deterministic preservation and unchanged historical opinions."""
import copy
import json
import unittest
from pathlib import Path
from review_agent.field_spec import prepare,specification,frozen,finding_errors,diagnostic_report
from review_agent import response_contract_v8 as v8,response_contract_v7 as v7
from review_agent.contracts import sha256_json,proposal_digest
from review_agent.validator_contracts import versions_for,validate_vote
from review_agent.response_corrections import make_plan,policy_for
from review_agent.request_cache import validation_request_key
from tests import test_review_four_axes as fixtures


class FieldSpecTests(unittest.TestCase):
    def setUp(self):
        self.candidate={'candidate_id':'c','revision':1,'triple':{'head':'A','surface_relation':'总是影响','tail':'B',
            'qualifiers':{'polarity':'positive','condition':'在试验条件下','applicability':None,'quantifier':'总是','modality':'asserted'},
            'qualifier_status':{'polarity':'extracted','condition':'extracted','applicability':'not_stated','quantifier':'extracted','modality':'extracted'}}}
        self.bundle={'items':[{'evidence_id':'e','anchor_valid':True,'quote':'在试验条件下，A总是影响B。'}]}
        self.bundle['evidence_hash']=sha256_json(self.bundle)

    def check(self):return prepare(self.candidate,self.bundle,specification())

    def test_frequency_and_duplicate_cleanup_preserve_condition_intensity_and_original(self):
        before=copy.deepcopy(self.candidate);report=self.check();result=report['normalized_expression']
        self.assertEqual(self.candidate,before)
        self.assertEqual(result['qualifiers']['frequency'],'总是')
        self.assertIsNone(result['qualifiers']['quantifier'])
        self.assertEqual(result['surface_relation'],'影响')
        self.assertEqual(result['qualifiers']['condition'],'在试验条件下')
        self.assertEqual([p['rule_id'] for p in report['plans']],['FREQ-001','DUP-001'])
        self.assertFalse(report['semantic_preservation_confirmed'])

    def test_mixed_conditions_cannot_be_migrated_by_substring(self):
        q=self.candidate['triple']['qualifiers'];q.update(quantifier=None,applicability='常常在高温条件下')
        self.candidate['triple']['qualifier_status']['applicability']='extracted'
        self.assertEqual(self.check()['plans'],[])
        self.assertEqual(self.check()['normalized_expression']['qualifiers']['applicability'],'常常在高温条件下')

    def test_foreign_or_unanchored_source_does_not_authorize_migration(self):
        self.bundle['items'][0]['anchor_valid']=False
        self.assertEqual(self.check()['plans'],[])

    def test_frequency_conflicts_and_tail_attachment_remain_semantic_obligations(self):
        self.candidate['triple']['qualifiers']['frequency']='偶尔'
        self.candidate['triple']['qualifier_status']['frequency']='extracted'
        self.assertFalse(self.check()['plans'])
        del self.candidate['triple']['qualifiers']['frequency']
        del self.candidate['triple']['qualifier_status']['frequency']
        self.candidate['triple']['semantic_context']={'qualifier_attachments':[{'value':'总是','attachment':'tail','scope_id':'s'}]}
        self.assertFalse(self.check()['plans'])

    def test_pairing_and_definition_modalities_are_not_automatically_rewritten(self):
        self.candidate['triple']['qualifiers'].update(quantifier='分别',modality='can')
        self.candidate['triple']['surface_relation']='可表示为'
        self.candidate['triple']['tail']='B和C'
        report=self.check()
        self.assertEqual(report['plans'],[])
        self.assertEqual({r['rule_id'] for r in report['semantic_review']},{'PAIR-001','MOD-001'})
        self.assertEqual(report['normalized_expression']['tail'],'B和C')
        self.assertEqual(report['normalized_expression']['qualifiers']['modality'],'can')

    def test_predicate_cannot_be_erased_and_paraphrases_are_not_deduplicated(self):
        self.candidate['triple']['surface_relation']='总是 '
        self.assertNotIn('DUP-001',[p['rule_id'] for p in self.check()['plans']])
        self.candidate['triple']['surface_relation']='始终影响'
        self.assertNotIn('DUP-001',[p['rule_id'] for p in self.check()['plans']])


class FieldSpecIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.FourAxisIntegrationTests('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def run_case(self,frequency=True):
        c,r,o=self.fixture.run_case(frequency=frequency,version=v8.CONTRACT_VERSION,mapping_preflight=True)
        a=self.fixture.fixture.repo.get_audit(c['candidate_id'],r['audit_id'])
        return c,r,o,a,a['rounds'][0]

    def test_all_judges_receive_one_frozen_spec_and_source_support_survives_normalization(self):
        c,r,o,a,p=self.run_case()
        self.assertTrue(r['independent_review_completed'],r)
        self.assertEqual(r['review_axes']['source']['status'],'supported')
        self.assertEqual(r['review_axes']['expression']['status'],'lossless_normalization')
        self.assertEqual(frozen(p)['normalized_expression']['qualifiers']['frequency'],'often')
        for v in o.validators:
            payload=v['client'].calls[0]
            self.assertEqual(payload['field_spec'],specification())
            self.assertEqual(payload['field_standardization'],p['field_standardization'])
        for vote in a['votes']:self.assertEqual(validate_vote(vote),[])
        stored=self.fixture.fixture.repo.get_candidate(c['candidate_id'])
        self.assertEqual(stored['triple']['qualifiers']['applicability'],'often')
        self.assertNotIn('frequency',stored['triple']['qualifiers'])
        self.assertNotEqual(r['admission_consensus']['result'],'accept_consensus')

    def test_binding_and_cache_identity_include_spec_and_derived_result(self):
        c,r,o,a,p=self.run_case()
        before=validation_request_key(o.validators[0],p)
        changed=copy.deepcopy(p);changed['field_spec']['fields']['frequency']='unconditional'
        with self.assertRaises(ValueError):frozen(changed)
        changed=copy.deepcopy(p);changed['field_standardization']['normalized_expression']['qualifiers']['condition']='changed'
        with self.assertRaises(ValueError):frozen(changed)
        changed=copy.deepcopy(p);changed['candidate']['triple']['tail']='other'
        with self.assertRaises(ValueError):frozen(changed)
        old=copy.deepcopy(p);old.update(versions_for({'validator_contract_version':v7.CONTRACT_VERSION}))
        for k in ('field_spec','field_spec_version','field_spec_hash','field_standardization','field_standardization_hash'):old.pop(k,None)
        from review_agent.output_structure import resolve_profile
        for generation,voter in zip(old['validator_generation'],o.validators):
            generation['output_transport']=resolve_profile(voter,{},v7.RESPONSE_SCHEMA)
        old['proposal_digest']=proposal_digest(old)
        self.assertNotEqual(before,validation_request_key(o.validators[0],old))

    def test_rule_impact_and_suggestion_are_required_and_semantics_cannot_be_repaired(self):
        c,r,o,a,p=self.run_case();raw=copy.deepcopy(a['votes'][0]['raw_response'])
        for field in ('rule_id','meaning_effect','suggested_change'):
            bad=copy.deepcopy(raw);bad['expression_findings'][0].pop(field)
            self.assertTrue(v8.validate_response(bad))
            plan,reasons=make_plan(bad,p,policy_for({}))
            self.assertIsNone(plan);self.assertTrue(reasons)
        bad=copy.deepcopy(raw);bad['expression_findings'][0]['meaning_effect']='changed'
        self.assertTrue(v8.validate_response(bad))
        bad=copy.deepcopy(raw);bad['expression_findings'][0]['rule_id']='MOD-001'
        self.assertTrue(v8.validate_response(bad))

    def test_invented_plans_foreign_evidence_and_silent_approval_are_rejected(self):
        c,r,o,a,p=self.run_case();raw=copy.deepcopy(a['votes'][0]['raw_response'])
        self.assertEqual(finding_errors(raw,p),[])
        raw['expression_findings'][0]['normalization']['plan_id']='made_up'
        self.assertTrue(finding_errors(raw,p))
        raw['expression_findings'][0]['evidence_ids']=['foreign']
        self.assertTrue(finding_errors(raw,p))
        raw['expression_findings']=[]
        self.assertTrue(finding_errors(raw,p))

    def test_multiple_rules_for_one_check_and_material_changes_stay_separate(self):
        c,r,o,a,p=self.run_case();raw=copy.deepcopy(a['votes'][0]['raw_response'])
        second=copy.deepcopy(raw['expression_findings'][0])
        second.update(rule_id='DUP-001',kind='duplicate_qualifier')
        second['normalization']['plan_id']='another_frozen_plan'
        raw['expression_findings'].append(second)
        self.assertEqual(v8.validate_response(raw),[])
        second.update(rule_id='MEANING-001',kind='semantic_error',meaning_effect='changed',normalization=None)
        raw['dimensions']['qualifiers']['status']='fail'
        self.assertEqual(v8.validate_response(raw),[])
        self.assertEqual(v8.normalize(raw)['review_axes']['source']['status'],'contradicted')

    def test_standard_new_review_and_old_contract_remain_distinct(self):
        c,r,o,a,p=self.run_case(frequency=False)
        self.assertEqual(r['review_axes']['expression']['status'],'standard')
        self.assertNotIn('rule_id',v7.RESPONSE_SCHEMA['properties']['expression_findings']['items']['properties'])
        self.assertNotEqual(sha256_json(v7.RESPONSE_SCHEMA),sha256_json(v8.RESPONSE_SCHEMA))
        self.assertTrue(all(v['client'].calls[0]['output_structure']['mode']=='json_template' for v in o.validators))

    def test_historical_diagnostic_is_bound_without_reclassifying_old_votes(self):
        c,r,o=self.fixture.run_case(frequency=True)
        f=self.fixture.fixture;a=f.repo.get_audit(c['candidate_id'],r['audit_id']);p=a['rounds'][0]
        report=prepare(p['candidate'],p['evidence_bundle'],specification())
        binding={'audit_id':p['audit_id'],'proposal_digest':p['proposal_digest'],'candidate_hash':sha256_json(p['candidate']),
            'evidence_hash':p['evidence_bundle']['evidence_hash'],'ontology_context_hash':p['ontology_context_hash']}
        entry={'binding':binding,'report':report,'report_hash':sha256_json(report)}
        self.assertTrue(diagnostic_report(entry,p)['post_run_diagnostic'])
        entry['binding']['candidate_hash']='other'
        self.assertIsNone(diagnostic_report(entry,p))

    def test_agent_ontology_tool_keeps_field_policy_separate_from_mapping(self):
        from review_agent.runtime import AgentRuntime
        c,r,o,a,p=self.run_case()
        runtime=AgentRuntime(o)
        runtime.initialize(c['candidate_id'],1,self.fixture.fixture.index,{})
        runtime.tools.inspect_ontology(None,'MOCK mapping context inspection')
        self.assertEqual(runtime.state.context['field_spec'],specification())
        self.assertIn('mapping_preflight',runtime.state.ontology_lookup)
        self.assertEqual(runtime.state.candidate['triple']['qualifiers']['applicability'],'often')


if __name__=='__main__':unittest.main()
