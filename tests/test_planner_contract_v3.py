import copy
import json
import unittest

from review_agent.action_policy import ActionPolicy
from review_agent.agent_state import AgentState,AgentBudget
from review_agent.planner_contracts import ActionError,argument_schemas,response_schema,validate_action


def action(name,**args):
    return {'action':name,'arguments':args,'target_issue':'check','rationale_summary':'inspect observed records'}


class PlannerContractV3Tests(unittest.TestCase):
    def state(self):
        state=AgentState(candidate={'candidate_id':'c','revision':1,'document_version':'doc1','triple':{}},context={},audit_id='a')
        evidence={'evidence_id':'ev_real','quote':'verified source','anchor_valid':True}
        state.archive=[evidence];state.bundle={'items':[evidence],'evidence_hash':'h'}
        return state

    def test_unverified_and_case_evidence_never_enter_reference_enums(self):
        state=self.state()
        state.archive.append({'evidence_id':'memory_fake','quote':'not current evidence','anchor_valid':False})
        payload=state.planner_input(AgentBudget({}),ActionPolicy({}).definitions())
        self.assertEqual(payload['valid_evidence_ids'],['ev_real'])
        self.assertNotIn('memory_fake',json.dumps(payload['output_schema']))
        with self.assertRaises(ActionError):
            ActionPolicy({}).validate_request(action('inspect_context',evidence_id='memory_fake',before=1,after=1),state)

    def test_source_support_consensus_is_required_before_schema_extension(self):
        state=self.state();state.ontology_checked=True;state.ontology_lookup={'scope':'full_frozen_ontology'}
        policy=ActionPolicy({})
        request=action('request_ontology_extension',change_kind='add_object_property',description='source-supported missing relation',
            proposed_changes={'label':'relation'},evidence_ids=['ev_real'])
        with self.assertRaisesRegex(ActionError,'independent source-support'): policy.validate_request(request,state)
        state.validations[state.validation_key()]={'consensus':{'source_support_consensus':{'result':'supported'},'eligible_voter_ids':['v']},
            'votes':[{'voter_id':'v','schema_issue':'not_covered','supporting_evidence_ids':['ev_real']}]}
        self.assertEqual(policy.validate_request(request,state),request)
        self.assertIn('request_ontology_extension',state.planner_input(AgentBudget({}),policy.definitions())['available_tools'])

    def test_extension_cannot_cite_valid_but_not_independently_supported_evidence(self):
        state=self.state();state.ontology_checked=True;state.ontology_lookup={'scope':'full_frozen_ontology'}
        state.archive.append({'evidence_id':'ev_other','quote':'another verified quote','anchor_valid':True})
        state.validations[state.validation_key()]={'consensus':{'source_support_consensus':{'result':'supported'},'eligible_voter_ids':['v']},
            'votes':[{'voter_id':'v','schema_issue':'not_covered','supporting_evidence_ids':['ev_real']}]}
        policy=ActionPolicy({})
        request=action('request_ontology_extension',change_kind='add_object_property',description='missing relation',
            proposed_changes={'label':'relation'},evidence_ids=['ev_other'])
        with self.assertRaisesRegex(ActionError,'source_supported_evidence_ids'): policy.validate_request(request,state)
        payload=state.planner_input(AgentBudget({}),policy.definitions())
        self.assertEqual(payload['source_supported_evidence_ids'],['ev_real'])
        self.assertEqual(payload['output_schema']['$defs']['request_ontology_extension']['properties']['evidence_ids']['items']['enum'],['ev_real'])

    def test_evidence_less_handoff_requires_ontology_and_attempted_search(self):
        state=self.state();state.archive=[];state.bundle={'items':[]}
        finish=action('finish_review',stop_reason='no verified evidence',unresolved_issues=['unverified source'],evidence_ids=[])
        policy=ActionPolicy({})
        with self.assertRaises(ActionError): policy.validate_request(finish,state)
        state.ontology_checked=True
        with self.assertRaisesRegex(ActionError,'search_evidence'): policy.validate_request(finish,state)
        state.searched_scopes=['current_chunk']
        policy.validate_request(finish,state)

    def test_planner_view_bounds_history_without_changing_complete_checkpoint(self):
        state=self.state();state.ontology_checked=True
        validation={'round_id':1,'proposal_digest':'p','evidence_hash':'h','consensus':{'valid_votes':3},
            'votes':[{'voter_id':'v','response_status':'ok','attempts':[{'raw_response':'X'*200000}]}]}
        state.validations[state.validation_key()]=validation;state.votes=validation['votes']
        state.observations=[{'step_no':n,'tool':'run_validation','status':'ok','validation':validation,'context':'Y'*200000} for n in range(12)]
        saved=copy.deepcopy(state.export())
        payload=state.planner_input(AgentBudget({}),ActionPolicy({}).definitions())
        self.assertLess(len(json.dumps(payload)),40000)
        self.assertEqual(len(payload['history']),6)
        self.assertEqual(payload['history'][-1]['validation']['consensus']['valid_votes'],3)
        self.assertEqual(state.export(),saved)

    def test_schema_and_python_validator_reject_extra_empty_tool_arguments(self):
        schema=argument_schemas()['run_validation']
        self.assertEqual(schema,{'type':'object','properties':{},'required':[],'additionalProperties':False})
        self.assertFalse(response_schema(argument_schemas())['additionalProperties'])
        with self.assertRaises(ActionError): validate_action(action('run_validation',evidence_ids=['ev_real']))


if __name__=='__main__': unittest.main()
