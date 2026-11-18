from copy import deepcopy
from threading import Event
import time
import unittest

from review_agent import revision_response_delivery as wire
from review_agent.revision_service import RESPONSE_SCHEMA, VERSION, judge_revision_once, revision_prompt, response_errors
from review_agent.contracts import sha256_json
from review_agent.llm_clients import MockLLMClient
from review_agent.agent_state import AgentBudget
from review_agent.consensus import Committee
from tests import test_review_recovery_v4 as fixtures


def proposal(new=True):
    value={'draft_id':'d','base_revision':1,'draft_hash':'h','proposal_digest':'p',
        'before':{'document_version':'doc','ontology_release_id':'rel','triple':{
            'qualifiers':{'polarity':'unknown'},'qualifier_status':{'polarity':'unknown'},'semantic_context':{}}},
        'after':{'triple':{'qualifiers':{'polarity':'positive'},'qualifier_status':{'polarity':'extracted'},'semantic_context':{'knowledge_scope':'conditional'}}},
        'changed_fields':['qualifiers.polarity','qualifier_status.polarity','semantic_context'],
        'evidence_bundle':{'items':[{'evidence_id':'ev'}],'evidence_hash':'evidence'},'evidence_ids':['ev'],'protected_evidence_ids':['ev'],
        'semantic_review_version':'full_expression_v2'}
    if new:value['revision_delivery_version']=wire.VERSION
    return value


def answer(p,decision='reject'):
    return {'decision':decision,'field_reviews':{f:{'status':'not_stated_in_scope','evidence_ids':['ev'],
        'note':'The source does not justify this proposed operation.'} for f in p['changed_fields']},
        'supporting_evidence_ids':[],'counter_evidence_ids':['ev'],'counterevidence_reviewed_ids':['ev'],
        'rationale_summary':'A substantive objection, not a format problem.'}


class RevisionDeliveryTests(unittest.TestCase):
    def test_required_value_and_status_fields_are_explicit_in_schema_and_checklist(self):
        p=proposal();contract=wire.output_contract(p,RESPONSE_SCHEMA)
        named,legacy=contract['properties']['field_reviews']['oneOf']
        self.assertEqual(named['required'],p['changed_fields'])
        self.assertFalse(named['additionalProperties'])
        self.assertEqual(legacy['minItems'],3)
        checklist=wire.request_fields(p)['field_review_checklist']
        self.assertEqual(checklist['qualifier_status.polarity'],{'before':'unknown','after':'extracted'})

    def test_projection_preserves_rejection_notes_and_all_evidence_exactly(self):
        p=proposal();raw=answer(p);original=deepcopy(raw)
        parsed,trace=wire.project(raw,p)
        self.assertEqual(raw,original)
        self.assertEqual(parsed['decision'],'reject')
        self.assertEqual(parsed['counter_evidence_ids'],['ev'])
        self.assertEqual(response_errors(parsed,p),[])
        for row in parsed['field_reviews']:
            self.assertEqual({k:v for k,v in row.items() if k!='field'},raw['field_reviews'][row['field']])
        self.assertEqual(trace['wire_hash'],sha256_json(raw))
        self.assertFalse(trace['semantic_values_changed'])

    def test_missing_status_review_is_rejected_without_backfilling(self):
        p=proposal();raw=answer(p);del raw['field_reviews']['qualifier_status.polarity']
        with self.assertRaisesRegex(ValueError,'exactly the changed fields'):wire.project(raw,p)
        voter={'id':'a','model_id':'mock','model_family':'fixture','client':MockLLMClient([raw])}
        result=judge_revision_once(voter,p)
        self.assertEqual(result['response_status'],'invalid_contract')
        self.assertEqual(result['raw_response']['decision'],'reject')
        self.assertNotIn('qualifier_status.polarity',result['raw_response']['field_reviews'])

    def test_canonical_array_still_requires_all_fields_and_genuine_rejection_is_valid(self):
        p=proposal();raw=answer(p)
        raw['field_reviews']=[{'field':f,**v} for f,v in raw['field_reviews'].items()]
        parsed,trace=wire.project(raw,p)
        self.assertIsNone(trace);self.assertEqual(response_errors(parsed,p),[])
        parsed['field_reviews'].pop(1)
        self.assertIn('every changed field requires exactly one review',response_errors(parsed,p))

    def test_legacy_request_prompt_hash_and_wire_format_are_unchanged(self):
        p=proposal(False)
        self.assertIs(wire.output_contract(p,RESPONSE_SCHEMA),RESPONSE_SCHEMA)
        self.assertEqual(wire.request_fields(p),{})
        self.assertEqual(wire.prompt_hash(p,revision_prompt(p),RESPONSE_SCHEMA,VERSION),sha256_json([VERSION,revision_prompt(p),RESPONSE_SCHEMA]))
        raw=answer(p);parsed,trace=wire.project(raw,p)
        self.assertIs(parsed,raw);self.assertIsNone(trace)
        self.assertIn('invalid field_reviews',response_errors(parsed,p))

    def test_named_response_runs_through_existing_judge_without_becoming_approval(self):
        p=proposal();voter={'id':'a','model_id':'mock','model_family':'fixture','client':MockLLMClient([answer(p)])}
        result=judge_revision_once(voter,p)
        self.assertEqual(result['response_status'],'ok')
        self.assertEqual(result['decision'],'reject')
        self.assertEqual(result['response_projection']['version'],wire.VERSION)
        sent=voter['client'].calls[0]
        self.assertEqual(set(sent['field_review_checklist']),set(p['changed_fields']))


class RevisionPerCallDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.RevisionAndConsumerTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_valid_named_rejections_finish_review_without_approval_or_format_recovery(self):
        def reject(p):
            eid=p['evidence_ids'][0]
            return {'decision':'reject','field_reviews':{f:{'status':'not_stated_in_scope','evidence_ids':[eid],
                'note':'The proposed change is not grounded.'} for f in p['changed_fields']},
                'supporting_evidence_ids':[],'counter_evidence_ids':[eid],
                'counterevidence_reviewed_ids':p['protected_evidence_ids'],'rationale_summary':'Substantive rejection.'}
        voters=self.f.fixture.voters
        for v in voters:v['client']._callable=reject
        before=[len(v['client'].calls) for v in voters]
        draft=self.f.draft()
        result=self.f.service.review(draft['draft_id'],voters,Committee(['a','b','c']),AgentBudget({'total_timeout_seconds':3}))
        self.assertEqual(result['status'],'needs_human')
        self.assertEqual(result['approved_voter_ids'],[])
        self.assertTrue(all(v['response_status']=='ok' and v['decision']=='reject' for v in result['votes']))
        self.assertEqual([len(v['client'].calls)-n for v,n in zip(voters,before)],[1,1,1])

    def test_budget_call_cap_is_not_overridden_by_longer_model_timeout(self):
        release=Event();started=Event();voters=self.f.fixture.voters
        def good(p):
            eid=p['evidence_ids'][0]
            return {'decision':'approve','field_reviews':[{'field':f,'status':'supported','evidence_ids':[eid],'note':'fixture'} for f in p['changed_fields']],
                'supporting_evidence_ids':[eid],'counter_evidence_ids':[],'counterevidence_reviewed_ids':p['protected_evidence_ids'],'rationale_summary':'fixture'}
        def slow(p):
            started.set();release.wait(1.0);return good(p)
        for v in voters:
            v['request_timeout_seconds']=2;v['client']._callable=good
        voters[0]['client']._callable=slow
        before=[len(v['client'].calls) for v in voters]
        draft=self.f.draft()
        try:
            result=self.f.service.review(draft['draft_id'],voters,Committee(['a','b','c']),
                AgentBudget({'total_timeout_seconds':3,'per_call_timeout_seconds':.15,'max_parallel_validators':1}))
            self.assertTrue(started.is_set())
            self.assertEqual(result['status'],'review_incomplete')
            self.assertTrue(result['votes'][0]['outcome_unknown'])
            self.assertEqual([len(v['client'].calls)-n for v,n in zip(voters,before)],[1,0,0])
            self.assertTrue(all(v['not_sent'] and v['actual_calls']==0 for v in result['votes'][1:]))
        finally:
            release.set()
            # Let the original worker persist its late receipt, without resending.
            limit=time.monotonic()+.6
            while time.monotonic()<limit:
                with self.f.repo.connect() as c:
                    running=c.execute("SELECT COUNT(*) FROM request_attempts WHERE state IN ('running','unknown')").fetchone()[0]
                if not running:break
                time.sleep(.01)


if __name__=='__main__':unittest.main()
