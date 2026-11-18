"""Unknown-field completion, annotation restoration and request accounting boundaries."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from review_agent.contracts import fact_key_for,sha256_json
from review_agent.llm_clients import MockLLMClient
from review_agent.qualifier_completion import propose,validate_completion_action
from review_agent.case_budget import CaseBudget
from tests import test_review_four_axes as fixtures


def action(changes,evidence):
    return {'action':'propose_revision','arguments':{'changes':changes,'evidence_ids':evidence,'reason':'affirmative source statement'},
            'target_issue':'unknown qualifiers','rationale_summary':'confirm only the missing field from cited source'}


class QualifierCompletionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.FourAxisIntegrationTests('runTest');self.f.setUp();self.addCleanup(self.f.doCleanups)
        c,_,self.owner=self.f.run_case(version='validator_response_v8',assigned_mapping=True,model_mapping='matched',weighting={'mode':'dynamic'})
        c=deepcopy(c);c['candidate_id']+='_unknown';c['triple']['qualifiers']['polarity']='unknown'
        c['triple']['qualifier_status']['polarity']='unknown';c['fact_key_state']='provisional';c['fact_key']=fact_key_for(c)
        self.c=c;self.repo=self.owner.repository;self.repo.add_candidate(c)
        self.document=self.f.fixture.index
        from evidence.hierarchical_retriever import retrieve_initial
        from evidence.verification import verify_evidence_bundle
        self.bundle=verify_evidence_bundle(retrieve_initial(c,self.document),self.document,c)
        self.ids=[e['evidence_id'] for e in self.bundle['items']]
        self.change={'qualifiers':{'polarity':'positive'},'qualifier_status':{'polarity':'extracted'}}
        self.case=self.repo.ensure_case(c['candidate_id'],{'llm_calls':8,'tokens':100000,'tool_calls':8})
        self.budget=CaseBudget({'max_llm_calls_per_candidate':8,'max_total_tokens':100000,'total_timeout_seconds':30},
            self.repo,[self.case],task_type='completion',task_id=c['candidate_id'])

    def test_only_unknown_paired_fields_can_be_proposed(self):
        self.assertEqual(validate_completion_action(action(self.change,self.ids),self.c,self.bundle)['arguments']['changes'],self.change)
        for changes in ({**self.change,'relation_id':'R_old'},
                        {'qualifiers':{'condition':'invented'},'qualifier_status':{'condition':'extracted'}},
                        {'qualifiers':{'polarity':'positive'},'qualifier_status':{}},
                        {'qualifiers':{'polarity':None},'qualifier_status':{'polarity':'not_stated'}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                validate_completion_action(action(changes,self.ids),self.c,self.bundle)

    def test_proposal_does_not_mutate_candidate_and_approval_requires_new_version(self):
        planner=MockLLMClient(lambda p:action(self.change,[p['evidence'][0]['evidence_id']]),model_id='planner',model_family='planner')
        before=sha256_json(self.repo.get_revision(self.c['candidate_id']))
        proposed=propose(self.repo,self.c,self.bundle,self.document,planner,self.budget,source_audit_id='completion-fixture')
        self.assertEqual(proposed['status'],'draft_proposed')
        self.assertEqual(sha256_json(self.repo.get_revision(self.c['candidate_id'])),before)
        from review_agent.revision_service import RevisionService
        def respond(payload,identity):
            uncertain=identity=='c';eid=payload['evidence_ids'][0]
            return {'decision':'uncertain' if uncertain else 'approve',
                'field_reviews':[{'field':f,'status':'unknown' if uncertain else 'supported',
                    'evidence_ids':[] if uncertain else [eid],'note':'MOCK qualifier source review'} for f in payload['changed_fields']],
                'supporting_evidence_ids':[] if uncertain else [eid],'counter_evidence_ids':[],
                'counterevidence_reviewed_ids':payload['protected_evidence_ids'],'rationale_summary':'MOCK independently assess affirmative source'}
        voters=[{'id':v,'model_id':v,'model_family':v,'client':MockLLMClient(lambda p,v=v:respond(p,v),model_id=v,model_family=v)} for v in 'abc']
        service=RevisionService(self.repo)
        reviewed=service.review(proposed['draft']['draft_id'],voters,self.owner.committee,self.budget)
        self.assertEqual(reviewed['status'],'approved',reviewed)
        self.assertEqual(reviewed['approved_voter_ids'],['a','b'])
        self.assertTrue(reviewed['weight_snapshot'])
        from review_agent.revision_service import revision_consensus
        draft=service.draft(proposed['draft']['draft_id'])
        review_proposal={k:v for k,v in draft.items() if k not in {'status','review_result'}}
        review_proposal.update(committee_members=reviewed['committee_members'],committee_policy=reviewed['committee_policy'])
        review_proposal['proposal_digest']=sha256_json(review_proposal)
        opposition=deepcopy(reviewed['votes'])
        other=next(v for v in opposition if v['voter_id']=='c');other['decision']='reject';other['counter_evidence_ids']=[self.ids[0]]
        self.assertFalse(revision_consensus(opposition,review_proposal)['approved'])
        applied=service.apply(proposed['draft']['draft_id'],proposed['draft']['draft_hash'])
        self.assertFalse(applied['old_votes_reusable']);self.assertTrue(applied['new_version_requires_independent_review'])
        new=self.repo.get_revision(self.c['candidate_id'])
        self.assertEqual(new['revision'],2);self.assertEqual(new['triple']['qualifiers']['polarity'],'positive')
        self.assertEqual(new['triple']['qualifiers']['condition'],self.c['triple']['qualifiers']['condition'])
        self.assertEqual(new['triple']['qualifier_status']['condition'],'not_stated')

    def test_agent_can_abstain_without_default_positive_or_revision(self):
        value={'action':'finish_review','arguments':{'stop_reason':'insufficient source','unresolved_issues':['polarity unclear'],'evidence_ids':[]},
               'target_issue':'unknown qualifier','rationale_summary':'no affirmative source claim'}
        result=propose(self.repo,self.c,self.bundle,self.document,MockLLMClient([value],model_id='planner',model_family='planner'),
                       self.budget,source_audit_id='completion-fixture')
        self.assertEqual(result['status'],'unresolved')
        self.assertEqual(self.repo.get_revision(self.c['candidate_id'])['triple']['qualifiers']['polarity'],'unknown')

    def test_unknown_request_remains_fenced_and_is_reported_without_resend(self):
        planner=MockLLMClient([RuntimeError('HTTP 504 gateway timeout')],model_id='planner',model_family='planner')
        first=propose(self.repo,self.c,self.bundle,self.document,planner,self.budget,source_audit_id='completion-fixture')
        second=propose(self.repo,self.c,self.bundle,self.document,planner,self.budget,source_audit_id='completion-fixture')
        self.assertEqual(first['status'],'proposal_unavailable');self.assertEqual(second['model_calls'],0)
        self.assertEqual(len(planner.calls),1)
        from review_agent.request_reconciliation import report
        result=report(self.repo,candidate_id=self.c['candidate_id'])
        self.assertEqual(result['execution_unknown_count'],1)
        self.assertFalse(result['requests'][0]['semantic_dependency'])
        self.assertFalse(result['requests'][0]['planner_action_applied'])
        self.assertFalse(result['original_request_states_changed'])

    def test_long_rationale_is_projected_without_changing_fields_or_resending(self):
        value=action(self.change,self.ids);value['arguments']['reason']='source evidence rationale '*30
        planner=MockLLMClient([value],model_id='planner',model_family='planner')
        result=propose(self.repo,self.c,self.bundle,self.document,planner,self.budget,source_audit_id='completion-fixture')
        self.assertEqual(result['status'],'draft_proposed')
        self.assertEqual(result['receipt']['response_status'],'invalid_action')
        self.assertEqual(len(planner.calls),1)
        self.assertEqual(result['bounded_projection']['changed_paths'],['/arguments/reason'])
        self.assertEqual(result['action']['arguments']['changes'],self.change)
        self.assertEqual(result['action']['arguments']['evidence_ids'],self.ids)
        self.assertLessEqual(len(result['action']['arguments']['reason']),400)
        self.assertFalse(result['bounded_projection']['semantic_fields_changed'])


class DefinitionRestorationTests(unittest.TestCase):
    def test_round_trip_preserves_source_definition_and_formal_identity(self):
        from rdflib import Graph,RDF,RDFS,OWL,URIRef,Literal
        from ontologyBuild.definition_materials import restoration_materials,restored_graph
        from ontologyBuild.owl_to_project_schema import convert_turtle
        node=URIRef('urn:test:connects');cls=URIRef('urn:test:Device')
        graph=Graph();graph.add((node,RDF.type,OWL.ObjectProperty));graph.add((node,RDFS.label,Literal('connects')))
        graph.add((node,RDFS.domain,cls));graph.add((node,RDFS.range,cls));graph.add((cls,RDF.type,OWL.Class));graph.add((cls,RDFS.label,Literal('Device')))
        original=convert_turtle(graph.serialize(format='turtle'));schema=original['project_schema']
        source={'candidate_id':'source-1','name':'connects','domain_candidate':'Device','range_candidate':'Device',
                'definition_candidate':'A connection explicitly stated between two devices.','evidence':[{'text':'A connects B.'}]}
        candidates={'candidate_object_properties':[source]};provenance=[{'iri':str(node),'candidate_id':'source-1'}]
        materials=restoration_materials(schema,candidates,provenance,'A connects B.')
        self.assertEqual(len(materials['definitions']),1);self.assertFalse(materials['automatic_semantic_approval'])
        restored=restored_graph(graph.serialize(format='turtle'),materials)
        new=convert_turtle(restored.serialize(format='turtle'),iri_id_map=original['iri_id_map'])['project_schema']
        identity=materials['definitions'][0]['relation_id']
        self.assertEqual(new['object_properties'][identity]['description'],source['definition_candidate'])
        for field in ('id','domain','range'):self.assertEqual(new['object_properties'][identity][field],schema['object_properties'][identity][field])
        conflicting=deepcopy(source);conflicting.update(candidate_id='source-2',definition_candidate='Different and incompatible definition.')
        candidates['candidate_object_properties'].append(conflicting);provenance.append({'iri':str(node),'candidate_id':'source-2'})
        conflict=restoration_materials(schema,candidates,provenance,'A connects B.')
        self.assertFalse(conflict['definitions']);self.assertEqual(conflict['unresolved'][0]['reason'],'conflicting_source_definitions')

    def test_exporter_annotation_helper_preserves_property_definition(self):
        from rdflib import Graph,URIRef,SKOS
        from ontologyBuild.definition_materials import add_definition
        graph=Graph();node=URIRef('urn:test:property')
        add_definition(graph,node,{'definition_candidate':'Explicit descriptive relation.'})
        self.assertEqual(str(next(graph.objects(node,SKOS.definition))),'Explicit descriptive relation.')


class RequestCorrelationTests(unittest.TestCase):
    def test_504_without_provider_id_keeps_client_correlation_and_unknown(self):
        import httpx
        from tests.test_transport_diagnostics import sdk_client
        from review_agent.execution import api_error_fields
        def handler(request):
            self.assertEqual(request.headers.get('X-Client-Request-Id'),'attempt-local-correlation')
            return httpx.Response(504,headers={'content-type':'text/plain'},text='gateway timeout')
        client=sdk_client(handler);client.client_request_id='attempt-local-correlation';self.addCleanup(client._client.close)
        with self.assertRaises(Exception) as raised:client.complete_json({})
        fields=api_error_fields(raised.exception)
        self.assertTrue(fields['outcome_unknown']);self.assertFalse(fields['retryable'])
        self.assertEqual(fields['transport_metrics']['client_request_id'],'attempt-local-correlation')
        self.assertIsNone(fields.get('gateway_request_id'))

    def test_revision_review_preserves_gateway_metadata_and_rejects_truncation(self):
        from unittest.mock import patch
        from review_agent.llm_clients import LLMResponse
        from review_agent.revision_service import judge_revision_once
        for content,finish,status in (('{bad','stop','invalid_contract'),('{}','length','output_incomplete')):
            response=LLMResponse(content,input_tokens=4,output_tokens=2,gateway_request_id='gw-revision',
                finish_reason=finish,transport_metrics={'client_request_id':'local-revision'})
            voter={'id':'a','client':MockLLMClient([response])}
            with patch('review_agent.revision_service.binding',return_value={'voter_id':'a'}):result=judge_revision_once(voter,{})
            self.assertEqual(result['response_status'],status)
            self.assertEqual(result['gateway_request_id'],'gw-revision')
            self.assertEqual(result['transport_metrics']['client_request_id'],'local-revision')
            self.assertEqual(result['input_tokens'],4)


if __name__=='__main__':unittest.main()
