"""Known failed deterministic seats recover without new proposals or valid-vote rerolls."""
from copy import deepcopy
import unittest
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.recovery_service import recover_frozen_audit
from tests import test_ontology_extension_agent as fixtures


class DeterministicRecoveryTests(unittest.TestCase):
    def test_v4_enum_recovery_receives_its_own_contract_without_legacy_fields(self):
        fixture=fixtures.OntologyExtensionTests(methodName='runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        candidate=deepcopy(fixture.candidate);candidate['candidate_id']='deterministic-v4-enum-recovery';fixture.repo.add_candidate(candidate)
        config=deepcopy(fixture.config);config['planner'].update(enabled=False,strategy='deterministic')
        config.update(validator_contract_version='validator_response_v4');config['consensus']['max_rounds']=1
        voters=fixture.source_voters
        for index,voter in enumerate(voters):
            original=voter['client']._callable
            def converted(payload,original=original,index=index):
                result=original(payload);ontology=next(c for c in result['checks'] if c['check']=='ontology')
                result['schema_assessment']={'status':result.pop('schema_status'),'issue':result.pop('schema_issue'),
                    'evidence_ids':ontology['evidence_ids'],'note':ontology['note']}
                result['checks']=[c for c in result['checks'] if c['check']!='ontology']
                if index==2:
                    feedback=payload.get('correction_feedback')
                    if not feedback:result['schema_assessment']['status']='not_covered'
                    else:
                        self.assertEqual(feedback['response_contract_version'],'validator_response_v4')
                        self.assertNotIn('not_covered',feedback['field_constraints']['schema_status_allowed'])
                        self.assertIn('not_covered',feedback['field_constraints']['schema_issue_allowed'])
                        self.assertNotIn('ontology_state',feedback['field_constraints'])
                return result
            voter['client']._callable=converted
        orchestrator=ReviewOrchestrator(fixture.repo,OntologyGuard(load_snapshot(fixture.base.ontology)),voters,config=config)
        first=orchestrator.audit_candidate(candidate['candidate_id'],1,fixture.index)
        self.assertFalse(first['independent_review_completed']);calls=[len(v['client'].calls) for v in voters]
        result=recover_frozen_audit(fixture.repo,orchestrator,candidate['candidate_id'],first['audit_id'],reason='Correct frozen v4 enum failure only.',extra_calls=1,extra_tokens=30000,extra_seconds=60)
        self.assertTrue(result['independent_review_completed'])
        self.assertEqual([len(v['client'].calls) for v in voters],[calls[0],calls[1],calls[2]+1])

    def test_exact_selected_proposal_recovers_one_failed_slot_without_planner_checkpoint(self):
        fixture=fixtures.OntologyExtensionTests(methodName='runTest')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        candidate=deepcopy(fixture.candidate);candidate['candidate_id']='deterministic-recovery-fixture'
        fixture.repo.add_candidate(candidate)
        config=deepcopy(fixture.config);config['planner'].update(enabled=False,strategy='deterministic')
        config['consensus']['max_rounds']=1;config['budget']['max_retries_per_request']=0
        voters=fixture.source_voters
        original=voters[2]['client']._callable
        def broken(payload):
            result=original(payload);result['checks']=[];return result
        voters[2]['client']._callable=broken
        orchestrator=ReviewOrchestrator(fixture.repo,OntologyGuard(load_snapshot(fixture.base.ontology)),voters,config=config)
        first=orchestrator.audit_candidate(candidate['candidate_id'],1,fixture.index)
        self.assertFalse(first['independent_review_completed'])
        audit=fixture.repo.get_audit(candidate['candidate_id'],first['audit_id'])
        self.assertFalse(audit.get('agent_state'))
        fixture.repo.update_audit_context(audit['audit_id'],{'issue_history':[{'issue_id':'inherited-expression-obligation',
            'code':'CONDITION_MISSING','opened_revision':candidate['revision']-1,'status':'open','critical':True,
            'requires_revision':True,'evidence_ids':[]}]})
        with fixture.repo.connect() as connection:
            frozen=[tuple(row) for row in connection.execute('SELECT round_id,proposal_json FROM proposals WHERE audit_id=? ORDER BY round_id',(audit['audit_id'],))]
        preserved={vote['voter_id']:deepcopy(vote) for vote in audit['votes'] if vote['response_status']=='ok'}
        calls=[len(v['client'].calls) for v in voters]
        voters[2]['client']._callable=original
        result=recover_frozen_audit(fixture.repo,orchestrator,candidate['candidate_id'],audit['audit_id'],
            reason='Recover a known deterministic contract failure under its original frozen proposal.',
            extra_calls=1,extra_tokens=30000,extra_seconds=60)
        self.assertTrue(result['independent_review_completed'])
        restored=fixture.repo.get_audit(candidate['candidate_id'],audit['audit_id'])
        self.assertEqual([len(v['client'].calls) for v in voters],[calls[0],calls[1],calls[2]+1])
        with fixture.repo.connect() as connection:
            self.assertEqual([tuple(row) for row in connection.execute('SELECT round_id,proposal_json FROM proposals WHERE audit_id=? ORDER BY round_id',(audit['audit_id'],))],frozen)
        self.assertEqual({v['voter_id']:v for v in restored['votes'] if v['voter_id'] in preserved},preserved)
        self.assertEqual(restored['context']['recovery_state_provenance']['kind'],'verified_selected_deterministic_proposal')
        self.assertEqual(restored['context']['issue_history'][0]['status'],'resolved')
        self.assertEqual(restored['context']['issue_history'][0]['resolution_rule'],'independent_new_revision_expression_recheck')
        self.assertFalse(restored['context']['unresolved_issues'])
        self.assertEqual(restored['agent_state']['state']['executed_strategy'],'deterministic')


if __name__=='__main__':unittest.main()
