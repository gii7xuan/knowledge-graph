"""No network: verified schema facts, actionable diagnostics and frozen prompts."""
from copy import deepcopy
from pathlib import Path
import unittest
from ontology_review import modeling_context as context
from ontology_review import validators
from ontology_review.contracts import CHECKS, versions, proposal_digest, vote_binding
from ontology_review.delta import catalog
from ontologyBuild.owl_to_project_schema import convert_turtle
from ontologyBuild.rdf_graph import parse_turtle
from ontologyBuild.rdf_artifact import graph_hash
from review_agent.contracts import sha256_json
from review_agent.llm_clients import MockLLMClient
from tests import test_ontology_extension_agent as fixtures

ROOT = Path(__file__).resolve().parents[1]


def proposal():
    base = {'classes': {i: {'id':i,'label':label,'description':label+' fixture concept'} for i,label in
            [('Air','air'),('Projectile','projectile'),('Energy','kinetic energy'),('Wave','wave'),('Shape','head shape'),('Head','head')]},
            'object_properties': {
                'Consumes': {'id':'Consumes','label':'consumes','domain':['Air'],'range':['Projectile'],'description':''},
                'WaveConsumes': {'id':'WaveConsumes','label':'consumes','domain':['Wave'],'range':['Energy'],'description':''}}}
    after=deepcopy(base);after['object_properties']['Consumes'].update(range=['Energy'],description='Consumes kinetic energy in the source context.')
    ops=[{'kind':'revise_constraint','target':'Consumes','changes':{'range':['Energy']},'evidence_ids':['ev']},
         {'kind':'revise_definition','target':'Consumes','changes':{'definition':after['object_properties']['Consumes']['description']},'evidence_ids':['ev']}]
    p={'task_type':'ontology_extension','task_id':'task','object_revision':1,'audit_id':'audit','round_id':1,
       'base_release_id':'base','base_ontology_version':'base-v','base_profile_hash':'profile','delta_hash':sha256_json(ops),
       'operations':ops,'base_schema':base,'merged_schema':after,'evidence':[{'evidence_id':'ev','quote':'Air consumes kinetic energy.'}],
       'evidence_hash':'evidence','check_records':{k:{'status':'pass','record_id':'check_'+k} for k in ('existing_schema','structure','impact','competency')},
       'versions':versions(),'committee_members':[{'id':c,'model_id':c,'model_family':c} for c in 'abc'],
       'committee_policy':{'committee_size':3,'required_votes':3,'minimum_distinct_families':3},'committee_hash':'committee'}
    p['modeling_context']=context.build(p);p['proposal_digest']=proposal_digest(p)
    return p


def response(decision='revise'):
    return {'decision':decision,'necessity':'confirmed_gap','semantic_status':'pass' if decision=='approve' else 'fail',
            'structure_status':'pass','impact_status':'pass','supporting_evidence_ids':['ev'],'counter_evidence_ids':[],
            'checks':[{'check':name,'status':'fail' if name=='definition' and decision!='approve' else 'pass',
                       'evidence_ids':['ev'],'note':'fixture','evidence_scope':'definition'} for name in CHECKS],
            'reason_codes':[],'suggested_changes':None,'requested_context':None}


class ModelingContextTests(unittest.TestCase):
    def test_quantity_class_and_existing_range_are_facts_without_semantic_approval(self):
        p=proposal();ctx=p['modeling_context'];cards={c['schema_id']:c for c in ctx['schema_reference_cards']}
        self.assertEqual(cards['Energy']['base']['schema_kind'],'class')
        self.assertEqual(cards['Projectile']['base']['label'],'projectile')
        self.assertEqual(cards['Consumes']['merged_changes']['range'],['Energy'])
        self.assertEqual(ctx['same_label_base_property_examples'][0]['range'],['Energy'])
        self.assertTrue(ctx['facts_are_not_source_or_semantic_approval'])
        self.assertFalse(ctx['peer_votes_included'])
        self.assertEqual(ctx['operation_reviews'][0]['before_changed_fields'],{'range':['Projectile']})
        self.assertEqual(ctx['operation_reviews'][0]['requested_changes_from'],'operations[0].changes')

    def test_tampered_context_is_blocked_before_any_provider_call(self):
        p=proposal();p['modeling_context']['schema_reference_cards'][0]['base']['label']='fabricated label'
        client=MockLLMClient([response('approve')]);vote=validators.judge_once({'id':'a','model_id':'a','model_family':'a','client':client},p)
        self.assertTrue(vote['not_sent']);self.assertEqual(vote['actual_calls'],0)
        self.assertEqual(vote['error_code'],'ONTOLOGY_MODELING_CONTEXT_PREFLIGHT_FAILED')
        self.assertFalse(vote['retryable']);self.assertEqual(client.calls,[])

    def test_missing_context_is_not_silently_rebuilt_after_freezing(self):
        p=proposal();del p['modeling_context']
        with self.assertRaisesRegex(ValueError,'missing or changed'):context.verify(p)

    def test_suggestions_flag_noop_restore_and_actual_reference_labels_without_editing_vote(self):
        p=proposal();raw=response();raw['suggested_changes']={'Consumes':{
            'definition':p['operations'][1]['changes']['definition'],'range':['Projectile']}}
        frozen=deepcopy(raw);diagnostics=context.suggestion_diagnostics(raw,p)
        self.assertEqual(raw,frozen)
        self.assertEqual({x['code'] for x in diagnostics},{'SUGGESTION_EQUALS_PROPOSED_STATE','SUGGESTION_RESTORES_BASE_FIELD','REFERENCED_SCHEMA_ID_LABELS'})
        refs=next(x['references'] for x in diagnostics if x['code']=='REFERENCED_SCHEMA_ID_LABELS')
        self.assertEqual(refs,[{'id':'Projectile','label':'projectile','schema_kind':'class'}])
        client=MockLLMClient([raw]);vote=validators.judge_once({'id':'a','model_id':'a','model_family':'a','client':client},p)
        self.assertEqual(vote['decision'],'revise');self.assertEqual(vote['response_status'],'ok')
        self.assertEqual(vote['suggestion_diagnostics'],diagnostics)

    def test_unknown_targets_and_references_are_diagnostics_not_automatic_rewrites(self):
        p=proposal();raw=response();raw['suggested_changes']={'Invented':{'range':['Energy']},'Consumes':{'range':['Unknown']}}
        codes={x['code'] for x in context.suggestion_diagnostics(raw,p)}
        self.assertIn('UNKNOWN_SUGGESTION_TARGET',codes);self.assertIn('UNRESOLVED_SUGGESTION_REFERENCES',codes)
        self.assertEqual(p['merged_schema']['object_properties']['Consumes']['range'],['Energy'])

    def test_valid_revise_with_noop_diagnostic_still_blocks_unanimity(self):
        p=proposal();votes=[]
        for member in p['committee_members']:
            raw=response('revise' if member['id']=='a' else 'approve')
            if member['id']=='a':raw['suggested_changes']={'Consumes':{'definition':p['operations'][1]['changes']['definition']}}
            votes.append(validators.judge_once({**member,'client':MockLLMClient([raw])},p))
        result=validators.consensus(votes,p)
        self.assertEqual(result['counts']['approve'],2);self.assertEqual(result['counts']['revise'],1)
        self.assertEqual(len(result['eligible_voter_ids']),3);self.assertEqual(result['result'],'unresolved')

    def test_historical_prompt_bytes_and_hashes_remain_exact(self):
        known={'ontology_extension_validator_v1':'ontology_extension_validator_v1.txt',
               'ontology_extension_validator_v2':'ontology_extension_validator_v2_legacy.txt',
               'ontology_extension_validator_v2_constraint_scope_v1':'ontology_extension_validator_v2.txt'}
        for version,name in known.items():
            text=(ROOT/'prompts'/name).read_text(encoding='utf8')
            self.assertEqual(validators.prompt_for({'versions':{'prompt_version':version,'prompt_hash':sha256_json(text)}}),text)
        self.assertEqual(validators.prompt_for({}),(ROOT/'prompts/ontology_extension_validator_v2.txt').read_text(encoding='utf8'))
        p=proposal();self.assertIn('表示层',validators.prompt_for(p))
        self.assertEqual(vote_binding({'id':'a','model_id':'a','model_family':'a'},p)['prompt_version'],context.PROMPT_VERSION)

    def test_unknown_prompt_or_wrong_hash_never_dispatches(self):
        for version,hash_value in [('unknown','bad'),('ontology_extension_validator_v2_constraint_scope_v1','bad')]:
            p=proposal();p['versions'].update(prompt_version=version,prompt_hash=hash_value)
            client=MockLLMClient([response('approve')]);vote=validators.judge_once({'id':'a','model_id':'a','model_family':'a','client':client},p)
            self.assertTrue(vote['not_sent']);self.assertEqual(vote['actual_calls'],0);self.assertEqual(client.calls,[])

    def test_rdf_projection_mismatch_cannot_supply_false_context_facts(self):
        before='''@prefix e: <urn:fixture:> . @prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
e:Air a owl:Class; rdfs:label "air" . e:Energy a owl:Class; rdfs:label "energy" .
e:R a owl:ObjectProperty; rdfs:label "consumes"; rdfs:domain e:Air; rdfs:range e:Air .'''
        after=before.replace('rdfs:range e:Air','rdfs:range e:Energy')
        b=convert_turtle(before);a=convert_turtle(after,iri_id_map=b['iri_id_map'])
        ids=b['iri_id_map'];p=proposal();p['base_schema']=b['project_schema'];p['merged_schema']=a['project_schema']
        p['operations']=[{'kind':'revise_constraint','target':ids['urn:fixture:R'],'changes':{'range':[ids['urn:fixture:Energy']]},'evidence_ids':['ev']}]
        p['source_patch']={'base_rdf':before,'merged_rdf':after,'base_graph_hash':graph_hash(parse_turtle(before)),'graph_hash':graph_hash(parse_turtle(after))}
        self.assertTrue(context.build(p)['schema_graph_binding']['schema_facts_reimported_and_verified'])
        p['base_schema']['classes'][ids['urn:fixture:Energy']]['label']='head entity'
        with self.assertRaisesRegex(ValueError,'facts differ'):context.build(p)

    def test_freeze_stores_context_in_a_new_proposal_without_changing_older_round(self):
        f=fixtures.OntologyExtensionTests('runTest');f.setUp();self.addCleanup(f.doCleanups);f.review()
        proposal=f.repo.ontology_audit(f.runtime.state['audit_id'])['proposals'][0]
        self.assertEqual(proposal['versions']['prompt_version'],context.PROMPT_VERSION)
        context.verify(proposal)
        self.assertEqual(proposal_digest(proposal),proposal['proposal_digest'])
        self.assertFalse(proposal['modeling_context']['peer_votes_included'])


if __name__=='__main__':unittest.main()
