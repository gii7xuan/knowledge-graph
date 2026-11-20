"""Grounded whole-expression edits and independent focused input budgets."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from review_agent.agent_state import AgentBudget
from review_agent.contracts import sha256_json
from review_agent.expression_revision import validate_revision_action


class ExpressionGroundingTests(unittest.TestCase):
    def setUp(self):
        from extract_triplets.candidate_contract import QUALIFIER_FIELDS
        self.candidate={'contract_version':'extraction_candidate_v4','triple':{
            'head':'frame','surface_relation':'chooses','tail':'a control volume','tail_kind':'entity',
            'qualifiers':{k:'positive' if k=='polarity' else 'asserted' if k=='modality' else None for k in QUALIFIER_FIELDS},
            'qualifier_status':{k:'extracted' if k in {'polarity','modality'} else 'not_stated' for k in QUALIFIER_FIELDS}}}
        self.bundle={'items':[{'evidence_id':'source','anchor_valid':True,
            'quote':'In this frame, we chose a control volume.'}]}
        self.action={'action':'propose_revision','target_issue':'actor','rationale_summary':'Correct the actor from source.',
            'arguments':{'changes':{'head':'we','surface_relation':'chose','tail':'the control volume'},
                'evidence_ids':['source'],'reason':'Source identifies the authors as the actor.',
                'field_evidence':{k:['source'] for k in ['head','surface_relation','tail']},
                'field_notes':{k:'Read the complete source statement and its referents.' for k in ['head','surface_relation','tail']}}}

    def test_head_tail_and_relation_all_accept_individual_source_evidence(self):
        before=deepcopy(self.candidate)
        fields=validate_revision_action(self.action,self.candidate,self.bundle)
        self.assertEqual(set(fields),{'head','surface_relation','tail'})
        self.assertEqual(self.candidate,before)

    def test_missing_field_proof_or_outside_source_is_rejected(self):
        del self.action['arguments']['field_evidence']['head']
        with self.assertRaisesRegex(ValueError,'each changed expression field'):
            validate_revision_action(self.action,self.candidate,self.bundle)
        self.action['arguments']['field_evidence']['head']=['unverified']
        with self.assertRaisesRegex(ValueError,'verified draft evidence'):
            validate_revision_action(self.action,self.candidate,self.bundle)

    def test_existing_qualifier_can_be_corrected_and_not_stated_needs_checked_scope(self):
        args=self.action['arguments']
        args['changes']={'qualifiers':{'modality':'can'},'qualifier_status':{'modality':'extracted'}}
        args['field_evidence']={'modality':['source']};args['field_notes']={'modality':'Source states ability.'}
        self.assertEqual(set(validate_revision_action(self.action,self.candidate,self.bundle)),{'qualifiers.modality'})
        args['changes']={'qualifiers':{'modality':None},'qualifier_status':{'modality':'not_stated'}}
        with self.assertRaisesRegex(ValueError,'inspected scope'):
            validate_revision_action(self.action,self.candidate,self.bundle)
        args['reviewed_scopes']={'modality':{'scope':'current_chunk','evidence_ids':['source']}}
        fields=validate_revision_action(self.action,self.candidate,self.bundle,checked_scopes={'current_chunk'})
        self.assertEqual(set(fields),{'qualifiers.modality','qualifier_status.modality'})

    def test_schema_exposes_whole_expression_field_proofs(self):
        from review_agent.planner_contracts import argument_schemas
        from review_agent.schema_validation import schema_errors
        schema=argument_schemas(True,True)['propose_revision']
        self.assertEqual(schema_errors(self.action['arguments'],schema),[])
        self.action['arguments']['changes']['head']=None
        with self.assertRaisesRegex(ValueError,'nonempty'):
            validate_revision_action(self.action,self.candidate,self.bundle)


class ExpressionWorkflowTests(unittest.TestCase):
    def test_whole_triple_draft_is_reviewed_applied_and_given_fresh_final_votes(self):
        import tests.test_resolution_workflows as workflows
        from review_agent.experiment_profiles import build_config
        fixture=workflows.ResolutionWorkflowTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        original_normalize=workflows.normalize_candidate;original_client=workflows.MockLLMClient
        originals=[]
        def normalize(raw,**kwargs):
            raw=deepcopy(raw);raw.update(head='Device B',surface_relation='is part of',tail='Device A')
            candidate=original_normalize(raw,**kwargs);originals.append(deepcopy(candidate));return candidate
        def client(responses,**kwargs):
            if kwargs.get('model_id')=='fixture_planner' and callable(responses):
                original=responses
                def wrapped(payload):
                    action=original(payload)
                    if action['action']=='propose_revision':
                        changes={'head':'Device A','surface_relation':'connects','tail':'Device B',
                            'qualifiers':{'modality':'can'},'qualifier_status':{'modality':'extracted'},
                            'relation_id':'R_old','head_type':'C','tail_type':'C'}
                        needed={k for k,v in changes.items() if k not in {'qualifiers','qualifier_status'} and originals[0]['triple'].get(k)!=v}|{'modality'}
                        args=action['arguments'];args.update(changes=changes,
                            field_evidence={k:args['evidence_ids'] for k in needed},
                            field_notes={k:'MOCK source separately supports this field in the complete statement.' for k in needed})
                    return action
                responses=wrapped
            return original_client(responses,**kwargs)
        with (patch.object(workflows,'build_config',lambda base,**kw:build_config(base,profile='handoff_revision')),
              patch.object(workflows,'normalize_candidate',normalize),patch.object(workflows,'MockLLMClient',client)):
            result=fixture.execute_case('revision')
        with fixture.f.repo.connect() as c:
            rows=c.execute('SELECT draft_json FROM revision_drafts WHERE candidate_id=?',(result['candidate']['candidate_id'],)).fetchall()
        self.assertEqual(len(rows),1);draft=json.loads(rows[0][0])
        self.assertTrue({'head','surface_relation','tail'}<=set(draft['changed_fields']))
        self.assertEqual(draft['after']['triple']['head'],'Device A')
        self.assertNotEqual(draft['before']['triple']['head_entity_id'],draft['after']['triple']['head_entity_id'])
        self.assertTrue(result['original_votes_unchanged'])
        self.assertEqual(result['final']['review_outcome']['recommendation'],'admit')
        final=fixture.f.repo.get_audit(result['candidate']['candidate_id'],result['final']['audit_id'])
        self.assertEqual({v['object_revision'] for v in final['votes']},{2})


class FocusedBudgetTests(unittest.TestCase):
    def setUp(self):
        from tests.test_claim_disputes import FocusedReviewTests
        self.fixture=FocusedReviewTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.initial_calls=[len(v['client'].calls) for v in self.fixture.voters]

    def review(self,limit=262144):
        from review_agent.dispute_review import review
        f=self.fixture
        return review(f.repo,f.base,f.dispute,f.bundle,f.voters,f.committee,
            AgentBudget({'max_evidence_chars':12000,'max_focused_input_bytes':limit,'total_timeout_seconds':30}))

    def test_evidence_json_over_12000_is_sent_intact_with_its_own_input_budget(self):
        f=self.fixture;f.bundle['local_metadata']='x'*13000
        self.assertGreater(len(json.dumps(f.bundle)),12000)
        result=self.review();self.assertEqual(result['status'],'resolved_supports',result)
        self.assertEqual(result['input_budget']['policy'],'focused_input_bytes_v1')
        for v in f.voters:self.assertEqual(v['client'].calls[-1]['evidence_bundle'],f.bundle)
        counts=[len(v['client'].calls) for v in f.voters]
        self.assertEqual(self.review(limit=1)['status'],'resolved_supports')
        self.assertEqual(counts,[len(v['client'].calls) for v in f.voters])

    def test_actual_local_budget_failure_is_not_sent_or_called_a_timeout(self):
        result=self.review(limit=1)
        self.assertEqual(result['error_code'],'FOCUSED_INPUT_BUDGET_EXCEEDED')
        self.assertTrue(result['not_sent']);self.assertFalse(result['outcome_unknown'])
        self.assertEqual(result['required_human_action'],'technical_recovery')
        self.assertEqual(result['model_calls'],0)
        f=self.fixture
        self.assertEqual([len(v['client'].calls) for v in f.voters],self.initial_calls)
        record={'dispute_id':f.dispute['dispute_id'],'focused_result':result}
        with f.repo.transaction() as c:
            c.execute('INSERT INTO agent_tool_calls VALUES(?,?,?,?,?,?,?)',
                ('budget-block',f.base['audit_id'],999,'review_dispute','human_review',json.dumps(record),'2026-11-20T00:00:00Z'))
        from review_agent.resolution_status import technical_tasks
        tasks=technical_tasks(f.repo,f.base['audit_id'])
        self.assertTrue(any(t['task_type']=='focused_input_budget' and not t['execution_unknown'] for t in tasks))

    def test_resolution_schema_requires_the_same_spillover_field_as_validation(self):
        from review_agent.dispute_review import schema_for,prompt_for
        proposal={'resolution_policy':'review_resolution_snapshot_v1'}
        self.assertIn('other_affected_checks',schema_for(proposal)['required'])
        self.assertIn('other_affected_checks',prompt_for(proposal))


if __name__=='__main__':unittest.main()
