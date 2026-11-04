"""Real bounded modeling investigation that cannot dispatch semantic votes or publish."""
from review_agent.agent_state import AgentStopped
from review_agent.repository import ConflictError
from .runtime import OntologyReviewRuntime


class OntologyPreparationRuntime(OntologyReviewRuntime):
    def reserved_validation_calls(self):
        return 0

    def planner_input(self,state,budget):
        payload=super().planner_input(state,budget)
        payload['operator_preparation_only']=True
        for field in ('target_issue','rationale_summary'):
            payload['output_schema']['properties'][field]['maxLength']=600
        payload['required_obligations'].append('Use a stop_reason of at most 400 characters and each rationale/target_issue of at most 600 characters; keep the full detailed observations in existing tool records.')
        payload['operation_contract']['new_class_references']='For a class added in this same complete delta, use its exact declared label in domain/range/parents; the server allocates its ID. Use exact existing formal IDs for existing classes. Never invent IDs or omit the corresponding add_class operation.'
        for kind in ('add_object_property','add_data_property'):
            payload['operation_contract']['changes_by_kind'][kind]=[
                'domain (existing class IDs or exact labels of classes added in this same delta)' if str(field).startswith('domain') else
                'range (existing class IDs or exact labels of classes added in this same delta)' if str(field).startswith('range') else field
                for field in payload['operation_contract']['changes_by_kind'][kind]]
        payload['required_obligations']=[o for o in payload['required_obligations'] if o!='run the full independent ontology committee after deterministic checks']
        payload['required_obligations'].append('All available evidence retrieval, schema neighborhood, minimal delta, structure and impact investigation tools are authorized. Preparation-only defers independent votes and publication; it does not prohibit investigation or proposing a grounded delta.')
        payload['required_obligations'].append('Preparation only: after real deterministic checks finish with a grounded handoff. No independent voting or publication is authorized in this preparation run.')
        return payload

    def execute_tool(self,action,state):
        observation=super().execute_tool(action,state)
        if action['action']=='propose_schema_delta' and state.get('delta'):
            state['stop_reason']='operator_preparation_delta_created_local_checks_follow'
        return observation

    def run(self,extension_id,audit_id=None):
        task=self.repository.extension_status(extension_id)
        if task.get('delta') is not None:raise ConflictError('preparation only accepts tasks without a current delta')
        with self.repository.connect() as c:
            frozen=c.execute("SELECT 1 FROM ontology_proposals p JOIN ontology_audits a ON a.audit_id=p.audit_id WHERE a.extension_id=? AND json_extract(p.proposal_json,'$.object_revision')=? LIMIT 1",(extension_id,task['delta_revision'])).fetchone()
        if frozen:raise ConflictError('current frozen review must be recovered, not replanned')
        return super().run(extension_id,audit_id)

    def run_validation(self):
        if not self.state.get('delta') or any(k not in self.state['checks'] for k in ('existing_schema','structure','impact','competency')):
            raise ValueError('complete real preparation checks required before stopping')
        self.state['stop_reason']='operator_preparation_complete_before_independent_review'
        raise AgentStopped(self.state['stop_reason'])

    def recover_validation(self):
        raise AgentStopped('preparation_mode_never_dispatches_reviewers')

    def finalize(self,state):
        if state.get('delta'):
            for name,method in (('structure','validate_schema_structure'),('impact','analyze_schema_impact'),('competency','run_competency_checks')):
                if name not in state['checks']:
                    if not self.budget.consume_tool():break
                    observation=getattr(self.tools,method)()
                    state['step_no']+=1
                    self.repository.ontology_step(state['audit_id'],state['step_no'],'tool',{
                        'action':{'action':method,'arguments':{}},'operator_preparation_only':True,**observation})
        result=super().finalize(state)
        result.update(operator_preparation_only=True,independent_review_completed=False,release_published=False)
        return result
