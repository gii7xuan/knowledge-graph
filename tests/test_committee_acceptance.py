from copy import deepcopy
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from evaluation.committee_acceptance import fixtures,signature,PRIMARY,PARALLEL,CASES
from document.hierarchy_index import split_markdown_text,build_document_index
from review_agent.validators import judge_independently
from review_agent.llm_clients import MockLLMClient


class CommitteeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.schema={'domain':'fixture','classes':{k:{'id':k,'label':label} for k,label in
            [('A','空气'),('B','理想气体'),('T','气温'),('D','空气密度')]},'object_properties':{
            PRIMARY:{'id':PRIMARY,'label':'近似看成','domain':['A'],'range':['B'],'definition':'空气可近似看成理想气体'},
            PARALLEL:{'id':PARALLEL,'label':'影响','domain':['T'],'range':['D'],'definition':'气温影响空气密度'}}}
        self.release=SimpleNamespace(ontology=self.schema,binding=lambda:{'ontology_version':'fixture-v1','ontology_release_id':'fixture-release','profile_hash':'p'})
        self.document=build_document_index(split_markdown_text('空气可近似看成理想气体。\n气温影响空气密度。','committee-fixture'))
        text=self.document['source_snapshot']['canonical_text']
        self.materials={'definitions':[]}
        for rid,quote in [(PRIMARY,'空气可近似看成理想气体'),(PARALLEL,'气温影响空气密度')]:
            start=text.index(quote);self.materials['definitions'].append({'relation_id':rid,
                'sources':[{'anchors':[{'start':start,'end':start+len(quote),'quote':quote}]}]})

    def make(self,run='fresh'):
        return fixtures(self.document,self.release,self.materials,run)

    def test_new_cases_cover_complete_predicate_error_and_missing_mapping(self):
        cases=self.make();self.assertEqual([x['name'] for x in cases],[c[0] for c in CASES])
        self.assertTrue(all(not x['candidate']['contract_issues'] for x in cases))
        positive,negative,missing,parallel=cases
        self.assertEqual(positive['candidate']['triple']['qualifiers']['modality'],'can')
        self.assertEqual(negative['candidate']['triple']['surface_relation'],'完全等同于')
        self.assertEqual(negative['candidate']['triple']['relation_id'],PRIMARY)
        self.assertIsNone(missing['candidate']['triple']['relation_id']);self.assertIsNone(missing['candidate']['triple']['tail_type'])
        self.assertEqual(missing['candidate']['triple']['qualifiers'],positive['candidate']['triple']['qualifiers'])
        self.assertEqual([x['max_parallel_validators'] for x in cases],[1,1,1,3])
        self.assertNotEqual(parallel['candidate']['triple']['head'],positive['candidate']['triple']['head'])

    def test_signature_cannot_be_bypassed_by_renaming_run_or_candidate(self):
        first=self.make('first');second=self.make('second')
        for a,b in zip(first,second):
            self.assertNotEqual(a['candidate']['candidate_id'],b['candidate']['candidate_id'])
            self.assertEqual(signature(a['candidate']),signature(b['candidate']))
        self.assertEqual(len({x['source_task_signature'] for x in first}),4)

    def test_changed_source_anchor_stops_preparation(self):
        self.materials['definitions'][0]['sources'][0]['anchors'][0]['quote']='invented source'
        with self.assertRaisesRegex(ValueError,'anchor changed'):self.make()


class SerialCommitteeDeadlineTests(unittest.TestCase):
    def test_serial_validator_queue_shares_one_execution_deadline(self):
        clients=[MockLLMClient(lambda _: (time.sleep(.2) or {})) for _ in range(3)]
        voters=[{'id':str(i),'client':c} for i,c in enumerate(clients)]
        proposal={'candidate':{'candidate_id':'c','revision':1,'triple':{'head':'A','surface_relation':'r','tail':'B'}},
            'audit_id':'a','round_id':1,'proposal_digest':'p','evidence_bundle':{'items':[]}}
        began=time.monotonic()
        votes=judge_independently(voters,proposal,max_workers=1,per_call_timeout_seconds=.08,deadline_epoch=time.time()+.10)
        self.assertLess(time.monotonic()-began,.24)
        self.assertEqual(len(votes),3);self.assertLess(sum(len(c.calls) for c in clients),3)
        self.assertEqual(votes[-1]['actual_calls'],0)

    def test_deterministic_orchestrator_passes_shared_deadline_to_validator_pool(self):
        from tests import test_field_spec as f
        helper=f.FieldSpecIntegrationTests('runTest');helper.setUp();self.addCleanup(helper.doCleanups)
        with patch('review_agent.orchestrator.judge_independently',wraps=judge_independently) as call:
            helper.run_case(frequency=False)
        self.assertIsInstance(call.call_args.kwargs['deadline_epoch'],float)


class RecordedCommitteeReplyTests(unittest.TestCase):
    def recorded(self,kind):
        from pathlib import Path
        import json
        value=json.loads((Path(__file__).parent/'fixtures/committee_reply_failures_20261117.json').read_text(encoding='utf-8'))
        return deepcopy(next(v['raw_response'] for v in value['responses'] if v['failure_kind']==kind))

    def check_immutable_failure(self,kind,expected):
        from review_agent.response_contract_v8 import validate_response
        from review_agent.contracts import sha256_json
        raw=self.recorded(kind);before=sha256_json(raw);errors=validate_response(raw)
        self.assertTrue(any(expected in e for e in errors),errors);self.assertEqual(sha256_json(raw),before)

    def test_mapping_sibling_ids_do_not_silently_become_a_bound_mapping(self):
        self.check_immutable_failure('mapping_fields_at_wrong_level','dimensions.mapping.relation_id')

    def test_unresolved_finding_cannot_be_counted_as_a_passing_check(self):
        self.check_immutable_failure('unresolved_finding_with_pass_dimension','unresolved')

    def test_material_counter_roles_remain_incompatible_with_no_counter_found(self):
        self.check_immutable_failure('counter_roles_with_pass_counter_check','counterevidence=pass')


if __name__=='__main__':unittest.main()
