"""General behavior regressions T01..T18; no production case-specific branches."""
import copy
import json
from pathlib import Path
import unittest
from types import SimpleNamespace

from document.hierarchy_index import split_markdown_text,build_document_index
from evidence.units import classify_quote,enrich_bundle
from evidence.verification import make_evidence_bundle,verify_evidence_bundle,verify_vote_references
from evidence.hierarchical_retriever import document_sentence_evidence
from review_agent.evidence_view import evidence_view
from review_agent.annotation_contract import import_annotation,training_record
from review_agent.response_contract_v5 import validate_response,CHECK_NAMES
from review_agent.revision_patch import merge_triple_patch
from review_agent.scope_policy import scope_route
from review_agent.ontology_guard import OntologyGuard,load_snapshot
from review_agent.validator_contracts import versions_for,prompt_for
from review_agent.contracts import sha256_json,fact_key_for
from schema.relation_registry import RelationRegistry
from extract_triplets.candidate_contract import normalize_candidate
from extract_triplets.pipeline_v4 import source_scope
from evaluation.split_integrity import verify_split
from tests.test_source_candidate_v4 import raw_candidate,ONTOLOGY


class SemanticRevisionTests(unittest.TestCase):
    def setUp(self):
        self.index=build_document_index(split_markdown_text('A relates B.\n$$\nx=y\n$$\n其中x表示位移。\nA does not measure C.','doc',600,20))
        self.candidate=normalize_candidate(raw_candidate(),index=self.index,scope=source_scope(self.index,self.index['chunks'][0]),
            ontology=ONTOLOGY,release_binding={'ontology_release_id':'rel','profile_hash':'prof','ontology_version':'v'},
            run_id='run',ordinal=0,prompt_hash='ph')
        items=[document_sentence_evidence(self.index,s) for s in self.index['sentences']]
        self.bundle=verify_evidence_bundle(make_evidence_bundle(items,[]),self.index,self.candidate)
        self.good=next(x for x in self.bundle['items'] if x['quote']=='A relates B.')
        self.marker=next(x for x in self.bundle['items'] if x['quote']=='$$')
        self.counter=next(x for x in self.bundle['items'] if 'does not' in x['quote'])
        self.proposal={'candidate':copy.deepcopy(self.candidate),'proposal_digest':'p','evidence_bundle':self.bundle}

    def response(self):
        eid=self.good['evidence_id']
        return {'verdict':'valid','source_support':'supported','schema_assessment':{'status':'pass','issue':'none','evidence_ids':[eid],'note':'formal definition checked'},
            'supporting_evidence_ids':[eid],'counter_evidence_ids':[],'counterevidence_reviewed_ids':[],'primary_evidence_ids':[eid],
            'reason_codes':[],'checks':[{'check':n,'status':'pass','evidence_ids':[eid],'issue_code':'none','note':'source checked'} for n in CHECK_NAMES],
            'requested_context':None,'suggested_revision':None,'schema_change_hint':None,'expression_status':'faithful','knowledge_scope':'general',
            'mapping':{'status':'matched','relation_id':'R','competing_relation_ids':[],'note':'formal property'},
            'evidence_assessments':[{'evidence_id':eid,'role':'support','note':'direct source'}]}

    def adjudication(self,**labels):
        return {'adjudicated_label':labels,'adjudication_status':'resolved','adjudicated_by':'human','reviewer':'reviewer',
            'annotated_at':'2026-11-04T12:00:00+08:00','saw_model_conclusions':False,'full_bundle_reviewed':True,
            'supporting_evidence_ids':[self.good['evidence_id']]}

    def test_T01_markers_cannot_support_but_complete_formula_is_retained(self):
        for q in ('$$','!','![](https://example.org/x.png)','https://example.org/x.jpg','https://example.org/image?id=123'):
            self.assertFalse(classify_quote(q)['substantive'])
        self.assertTrue(classify_quote('$$\nx=y\n$$')['substantive'])
        upgraded=enrich_bundle(self.bundle,self.index,self.candidate)
        self.assertIn('$$\nx=y\n$$',[x['quote'] for x in upgraded['items']])
        vote=verify_vote_references({'response_status':'ok','response_contract_version':'validator_response_v5',
            'supporting_evidence_ids':[self.marker['evidence_id']]},self.bundle['items'])
        self.assertEqual(vote['response_status'],'invalid_reference')
        fragment=next(x for x in upgraded['items'] if x['quote']=='x=y')
        self.assertEqual(fragment['block_type'],'equation_fragment')
        self.assertFalse(fragment['substantive'])

    def test_T02_reviewed_primary_quote_and_counterevidence_are_both_visible(self):
        vote={'response_status':'ok','source_support':'supported','expression_status':'faithful','primary_evidence_ids':[self.good['evidence_id']],
            'supporting_evidence_ids':[self.marker['evidence_id'],self.good['evidence_id']],
            'counter_evidence_ids':[self.counter['evidence_id']]}
        view=evidence_view(self.bundle,[vote],authoritative=True)
        self.assertEqual(view['primary_evidence']['quote'],'A relates B.')
        self.assertEqual(view['counter_evidence'][0]['quote'],self.counter['quote'])
        self.assertIsNone(evidence_view(self.bundle,[vote])['primary_evidence'])

    def test_T03_expanded_package_gets_new_hash_without_rewriting_old_ids(self):
        before=copy.deepcopy(self.bundle);expanded=enrich_bundle(before,self.index,self.candidate)
        self.assertNotEqual(before['evidence_hash'],expanded['evidence_hash'])
        self.assertEqual(before,self.bundle)
        for item in before['items']:
            matching=next(x for x in expanded['items'] if x['evidence_id']==item['evidence_id'])
            self.assertEqual(matching['quote'],item['quote'])
        self.assertEqual(evidence_view(expanded)['evidence_hash'],expanded['evidence_hash'])

    def test_T04_conflicting_human_claim_is_preserved_pending(self):
        raw={'来源支持':'反驳','事实表述正确':'否'}
        a=import_annotation(raw,self.candidate,self.proposal,adjudication={
            'observed_supporting_evidence_ids':[self.good['evidence_id']]})
        self.assertEqual(a['raw_annotation'],raw);self.assertEqual(a['adjudication_status'],'pending')
        self.assertIn('HUMAN_SOURCE_SUPPORT_CONFLICT',a['reason_codes'])

    def test_T05_mapping_yes_without_formal_id_is_not_verified(self):
        c=copy.deepcopy(self.candidate);c['triple']['relation_id']=None
        a=import_annotation({'关系映射正确':'是'},c,{**self.proposal,'candidate':c},
            adjudication=self.adjudication(mapping_status='correct'))
        self.assertEqual(a['adjudication_status'],'pending');self.assertIsNone(training_record(a,'mapping_status'))

    def test_T06_insufficient_is_never_a_contradiction_training_label(self):
        a=import_annotation({'来源支持':'证据不足'},self.candidate,self.proposal,
            adjudication=self.adjudication(source_support='insufficient'))
        self.assertEqual(training_record(a,'source_support')['label'],'insufficient')
        self.assertEqual(a['adjudicated_label']['expression_status'],'unresolved')

    def test_T07_entity_scope_revision_retains_original_mention(self):
        original={'head':'sensor surface','semantic_context':{'origin_mentions':{'head':{'entity_surface':'surface'}},'knowledge_scope':'general'}}
        result=merge_triple_patch(original,{'head':'sensor surface','semantic_context':{
            'entity_mentions':{'head':{'entity_surface':'surface','canonical_entity':'sensor surface','referent_scope':'sensor'}},'knowledge_scope':'general'}})
        self.assertEqual(result['semantic_context']['origin_mentions']['head']['entity_surface'],'surface')
        self.assertEqual(original['semantic_context']['origin_mentions']['head']['entity_surface'],'surface')

    def test_T08_predicate_edit_does_not_swap_endpoints(self):
        result=merge_triple_patch({'head':'A','tail':'B','surface_relation':'called'}, {'surface_relation':'defined as'})
        self.assertEqual((result['head'],result['tail']),('A','B'))
        value=self.response();next(c for c in value['checks'] if c['check']=='relation_direction').update(status='unknown')
        self.assertTrue(validate_response(value))

    def test_T09_generic_predicate_still_requires_complete_independent_expression_check(self):
        from review_agent.revision_service import revision_prompt,PROMPT
        self.assertEqual(revision_prompt({}),PROMPT)
        self.assertIn('COMPLETE before/after',revision_prompt({'semantic_review_version':'full_expression_v2'}))
        raw=self.response();raw.update(verdict='uncertain',expression_status='needs_revision')
        next(c for c in raw['checks'] if c['check']=='relation_semantics').update(status='fail',issue_code='LOSS_OF_SPECIFIC_PROPERTY')
        self.assertEqual(validate_response(raw),[])

    def test_T10_qualifier_attachment_is_kept_on_its_branch(self):
        context={'knowledge_scope':'conditional','qualifier_attachments':[
            {'qualifier_kind':'condition','attachment':'branch_2','value':'supersonic','scope_id':'section'}]}
        original={'head':'A','tail':'B','semantic_context':context}
        updated=merge_triple_patch(original,{'surface_relation':'contains'})
        self.assertEqual(updated['semantic_context']['qualifier_attachments'],context['qualifier_attachments'])
        with self.assertRaises(ValueError): merge_triple_patch(original,{'semantic_context':{'qualifier_attachments':[{'value':'supersonic'}]}})

    def test_T11_unknown_polarity_never_fills_itself(self):
        from extract_triplets.candidate_contract import normalize_qualifiers
        values,states,_=normalize_qualifiers({'qualifiers':{},'qualifier_status':{}})
        self.assertEqual(values['polarity'],'unknown');self.assertEqual(states['polarity'],'unknown')

    def test_T12_same_name_relations_expose_definitions_without_guessing(self):
        ontology=copy.deepcopy(ONTOLOGY);ontology['object_properties']['R2']={**ontology['object_properties']['R'],'id':'R2','definition':'a distinct relation'}
        result=RelationRegistry(ontology).lookup('relates')
        self.assertEqual(result['status'],'ambiguous');self.assertIsNone(result['relation_id'])
        self.assertEqual(len(result['candidates']),2)
        self.assertFalse(all(c['definition_available'] for c in result['candidates']))

    def test_T13_constraint_failure_exposes_requested_type_range_and_paths(self):
        ontology={'classes':{'A':{'label':'parent'},'B':{'label':'child','parents':['A']},'X':{'label':'other'}},
            'object_properties':{'R':{'id':'R','label':'r','domain':['A'],'range':['A']}}}
        guard=OntologyGuard(load_snapshot(ontology))
        check=guard.check({'triple':{'relation_id':'R','head_type':'B','tail_type':'X'}})
        self.assertEqual(check['status'],'fail');self.assertEqual(check['requested_tail_type'],'X')
        self.assertEqual(check['allowed_range'],['A']);self.assertEqual(check['hierarchy_paths']['head']['A'],['B','A'])
        self.assertEqual(check['hierarchy_paths']['tail']['A'],[])

    def test_T14_scope_exclusion_preserves_source_truth(self):
        result=scope_route('supported','faithful','unassigned','local_definition',scope_confirmed=True)
        self.assertEqual(result['workflow_state'],'excluded_by_scope');self.assertEqual(result['source_support'],'supported')
        self.assertNotEqual(scope_route('supported','faithful','unassigned','local_definition')['workflow_state'],'excluded_by_scope')

    def test_T15_annotations_and_fact_identity_do_not_transfer_to_new_revision(self):
        after=copy.deepcopy(self.candidate);after['revision']+=1;after['triple']['head']='new object'
        with self.assertRaises(ValueError): import_annotation({},after,self.proposal)
        a=import_annotation({},self.candidate,self.proposal)
        self.assertEqual(a['binding']['revision'],1)
        scoped=copy.deepcopy(self.candidate);scoped.update(fact_identity_version='fact_identity_v3',fact_key_state='canonical')
        scoped['triple']['semantic_context']={'scope_id':'s1','knowledge_scope':'local_definition'}
        first=fact_key_for(scoped);scoped['triple']['semantic_context']['scope_id']='s2'
        self.assertNotEqual(first,fact_key_for(scoped))

    def test_T16_pending_adjudication_is_excluded_from_training(self):
        a=import_annotation({},self.candidate,self.proposal,adjudication={'adjudicated_label':{'source_support':'supported'}})
        self.assertIsNone(training_record(a,'source_support'))
        a=import_annotation({},self.candidate,self.proposal,adjudication=self.adjudication(source_support='supported'))
        self.assertEqual(training_record(a,'source_support')['label'],'supported')

    def test_T17_production_modules_do_not_branch_on_development_ids(self):
        root=Path(__file__).resolve().parents[1]
        for directory in ('review_agent','evidence','schema','extract_triplets'):
            for p in (root/directory).glob('*.py'):
                self.assertNotRegex(p.read_text(encoding='utf-8'),r'DEV-\d{3}|cand_ca27ef4e78caeece9179',str(p))

    def test_T18_versions_and_grouped_test_split_are_frozen(self):
        v=versions_for({'validator_contract_version':'validator_response_v5'})
        self.assertEqual(v['evidence_bundle_version'],'evidence_bundle_v3')
        self.assertEqual(prompt_for(v),__import__('review_agent.response_contract_v5',fromlist=['SYSTEM_PROMPT']).SYSTEM_PROMPT)
        a=copy.deepcopy(self.candidate);b=copy.deepcopy(a);b['candidate_id']='another';b['revision']=2
        with self.assertRaises(ValueError): verify_split([a,b],{(a['candidate_id'],1):'development',('another',2):'independent_test'})

    def test_counterevidence_role_conflict_is_invalid_contract_not_negative_fact(self):
        raw=self.response();raw['verdict']='uncertain';raw['counter_evidence_ids']=[self.counter['evidence_id']]
        raw['evidence_assessments'].append({'evidence_id':self.counter['evidence_id'],'role':'context','note':'not a real contradiction'})
        errors=validate_response(raw)
        self.assertTrue(any('counterevidence=pass' in e for e in errors))
        self.assertTrue(any('consistent evidence_assessments role' in e for e in errors))

    def test_pass_without_qualifier_citation_is_contract_error(self):
        raw=self.response();next(c for c in raw['checks'] if c['check']=='qualifiers')['evidence_ids']=[]
        self.assertTrue(any('qualifiers' in e for e in validate_response(raw)))

    def test_legacy_support_lists_do_not_claim_a_complete_primary_set(self):
        view=evidence_view(self.bundle,[{'response_status':'ok','supporting_evidence_ids':[self.good['evidence_id']]}],authoritative=True)
        self.assertEqual(view['main_evidence_status'],'pending_verification')
        self.assertIn(self.good['quote'],[x['quote'] for x in view['body_fragments']])

    def test_partial_semantic_revision_preserves_tail_reference_and_local_qualifier(self):
        old={'semantic_context':{'entity_mentions':{'head':{'entity_surface':'surface','canonical_entity':'surface','referent_scope':None},
            'tail':{'entity_surface':'air','canonical_entity':'air','referent_scope':'chapter'}},'knowledge_scope':'conditional',
            'qualifier_attachments':[{'qualifier_kind':'condition','attachment':'relation','value':'calibrated','scope_id':'s'}]}}
        new=merge_triple_patch(old,{'semantic_context':{'entity_mentions':{'head':{'canonical_entity':'sensor surface','referent_scope':'sensor'}}}})
        self.assertEqual(new['semantic_context']['entity_mentions']['head']['entity_surface'],'surface')
        self.assertEqual(new['semantic_context']['entity_mentions']['tail'],old['semantic_context']['entity_mentions']['tail'])
        self.assertEqual(new['semantic_context']['qualifier_attachments'],old['semantic_context']['qualifier_attachments'])

    def test_adding_referent_metadata_to_legacy_expression_keeps_the_old_surface(self):
        old={'head':'surface','tail':'air'}
        new=merge_triple_patch(old,{'head':'sensor surface','semantic_context':{
            'entity_mentions':{'head':{'entity_surface':'sensor surface','canonical_entity':'sensor surface','referent_scope':'sensor'}},
            'knowledge_scope':'general'}})
        self.assertEqual(new['semantic_context']['origin_mentions']['head']['entity_surface'],'surface')

    def test_near_duplicate_from_another_document_cannot_be_an_independent_test(self):
        a=copy.deepcopy(self.candidate);b=copy.deepcopy(a)
        b.update(candidate_id='cross_doc',document_id='other_document',document_version='other_version')
        with self.assertRaises(ValueError):
            verify_split([a,b],{(a['candidate_id'],1):'development',('cross_doc',1):'independent_test'})

    def test_literal_and_proposition_tails_do_not_inherit_entity_class_hints(self):
        for kind in ('literal','event','proposition'):
            raw=raw_candidate();raw.update(tail_kind=kind,tail_type_candidate='C',value='B',datatype='string')
            c=normalize_candidate(raw,index=self.index,scope=source_scope(self.index,self.index['chunks'][0]),ontology=ONTOLOGY,
                release_binding={},run_id='r',ordinal=0,prompt_hash='h',require_semantic_context=True)
            self.assertIsNone(c['triple']['tail_type']);self.assertEqual(c['extraction_hints']['tail_type_candidate'],'C')

    def test_text_waiver_cannot_turn_missing_or_format_only_evidence_into_gold(self):
        a=self.adjudication(source_support='supported');a.update(supporting_evidence_ids=[],note='accept',resolved_issue_codes=['SUPPORTING_EVIDENCE_REQUIRED'])
        record=import_annotation({},self.candidate,self.proposal,adjudication=a)
        self.assertEqual(record['adjudication_status'],'pending')
        a.update(supporting_evidence_ids=[self.marker['evidence_id']],resolved_issue_codes=['NON_SUBSTANTIVE_SUPPORT_REFERENCE'])
        self.assertIsNone(training_record(import_annotation({},self.candidate,self.proposal,adjudication=a),'source_support'))

    def test_new_negative_adjudication_also_records_model_support_conflict(self):
        a=self.adjudication(source_support='contradicted');a.update(semantic_error='subject role is wrong',
            observed_supporting_evidence_ids=[self.good['evidence_id']])
        record=import_annotation({},self.candidate,self.proposal,adjudication=a)
        self.assertEqual(record['adjudication_status'],'pending')
        self.assertIn('HUMAN_SOURCE_SUPPORT_CONFLICT',record['reason_codes'])

    def test_class_existence_alone_does_not_prove_formal_schema_coverage(self):
        ontology=copy.deepcopy(ONTOLOGY);ontology['classes']['C']['definition']='device kind'
        ontology['object_properties']['R']['definition']='relation between devices'
        c=copy.deepcopy(self.candidate);c['triple'].update(head_type='C',tail_type='C')
        a=self.adjudication(schema_coverage='covered')
        a['coverage_evidence']={'term_ids':['C'],'definitions':{'C':'device kind'},'type_paths':[['C']]}
        record=import_annotation({},c,{**self.proposal,'candidate':c},adjudication=a,ontology=ontology)
        self.assertEqual(record['adjudication_status'],'pending')
        a['coverage_evidence'].update(relation_id='R',term_ids=['C','R'],definitions={'C':'device kind','R':'relation between devices'})
        record=import_annotation({},c,{**self.proposal,'candidate':c},adjudication=a,ontology=ontology)
        self.assertEqual(record['adjudication_status'],'resolved')

    def test_experiment_entry_rejects_development_test_near_duplicate_before_execution(self):
        from evaluation.experiment import run_experiment
        dev=copy.deepcopy(self.candidate);dev.update(candidate_id='dev_reference',document_id='development_doc')
        spec={'split':{'train_document_ids':[],'test_document_ids':['doc'],'development_document_ids':['development_doc']},
              'groups':[{'id':'rules','method':'rules'}]}
        with self.assertRaisesRegex(ValueError,'related facts'):
            run_experiment(spec,[self.candidate],[],'unused_release','unused_output',{},development_candidates=[dev])


class V5IntegrationTests(unittest.TestCase):
    def setUp(self):
        from tests.test_ontology_extension_agent import OntologyExtensionTests
        fixture=OntologyExtensionTests(methodName='runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.fixture=fixture

    def run_review(self,scope='general',expression='faithful'):
        from review_agent.llm_clients import MockLLMClient
        from review_agent.orchestrator import ReviewOrchestrator
        f=self.fixture;c=copy.deepcopy(f.candidate);c['candidate_id']+='_new_contract';c['fact_key']=fact_key_for(c)
        f.repo.add_candidate(c);seen=[]
        def vote(payload):
            seen.append(copy.deepcopy(payload));eid=next(e['evidence_id'] for e in payload['evidence'] if 'A regulates B.' in e['quote'])
            raw={'verdict':'uncertain','source_support':'supported','schema_assessment':{'status':'unknown','issue':'candidate_mapping_error','evidence_ids':[eid],'note':'unassigned formal mapping'},
                'supporting_evidence_ids':[eid],'counter_evidence_ids':[],'counterevidence_reviewed_ids':[],'primary_evidence_ids':[eid] if expression=='faithful' else [],
                'reason_codes':[],'checks':[{'check':n,'status':'unknown' if n=='mapping' else 'pass','evidence_ids':[eid],'issue_code':'none','note':'verified source scope'} for n in CHECK_NAMES],
                'requested_context':None,'suggested_revision':None,'schema_change_hint':None,'expression_status':expression,'knowledge_scope':scope,
                'mapping':{'status':'unassigned','relation_id':None,'competing_relation_ids':[],'note':'needs formal mapping'},
                'evidence_assessments':[{'evidence_id':eid,'role':'support','note':'direct statement'}]}
            if expression=='needs_revision':
                next(x for x in raw['checks'] if x['check']=='entity_reference').update(status='fail',issue_code='ENTITY_MISMATCH')
            return raw
        voters=[{'id':v,'model_id':'isolated_'+v,'model_family':v,'client':MockLLMClient(vote,model_id='isolated_'+v,model_family=v)} for v in 'abc']
        config={**f.config,'validator_contract_version':'validator_response_v5','planner':{'enabled':False,'strategy':'deterministic'},
                'ontology_extension':{'enabled':False},'consensus':{**f.config['consensus'],'max_rounds':1}}
        result=ReviewOrchestrator(f.repo,OntologyGuard(load_snapshot(f.base.ontology)),voters,config=config).audit_candidate(c['candidate_id'],1,f.index)
        return c,result,seen

    def test_v5_full_committee_mapping_route_and_frozen_export(self):
        from evaluation.review_samples import freeze_review_sample
        from review_agent.service import ReviewService
        c,result,payloads=self.run_review()
        self.assertEqual(result['source_support_result'],'supported')
        self.assertEqual(result['workflow_state'],'mapping_review')
        self.assertEqual(result['consensus']['source_support_consensus']['support_votes'],3)
        self.assertEqual(len(payloads),3)
        self.assertEqual(len({sha256_json(p['evidence']) for p in payloads}),1)
        for p in payloads:
            self.assertNotIn('votes',p);self.assertNotIn('human_labels',p)
            self.assertIn('ontology_catalog',p)
        exported=freeze_review_sample(self.fixture.repo,c['candidate_id'])
        self.assertEqual(exported['binding']['evidence_hash'],result['selected_evidence_hash'])
        audit=self.fixture.repo.get_audit(c['candidate_id'],result['audit_id'])
        p=next(x for x in audit['rounds'] if x['proposal_digest']==result['selected_proposal_digest'])
        eid=next(x['evidence_id'] for x in p['evidence_bundle']['items'] if 'A regulates B.' in x['quote'])
        service=ReviewService(self.fixture.repo,config={})
        record=service.import_annotation({'candidate_id':c['candidate_id'],'revision':1,'audit_id':audit['audit_id'],
            'proposal_digest':p['proposal_digest'],'evidence_hash':p['evidence_bundle']['evidence_hash'],
            'raw_annotation':{'source_support':'supported'},'adjudication':{
                'adjudicated_label':{'source_support':'supported'},'adjudication_status':'resolved','adjudicated_by':'human',
                'reviewer':'tester','annotated_at':'2026-11-04','saw_model_conclusions':False,'full_bundle_reviewed':True,
                'supporting_evidence_ids':[eid]}})
        self.assertEqual(record['adjudication_status'],'resolved')
        self.assertEqual(self.fixture.repo.get_revision(c['candidate_id']),c)
        self.assertIsNone(self.fixture.repo.get_candidate(c['candidate_id'])['pending_extension_id'])

    def test_v5_scope_exclusion_preserves_actual_source_consensus(self):
        c,result,_=self.run_review(scope='local_definition')
        self.assertEqual(result['source_support_result'],'supported')
        self.assertEqual(result['workflow_state'],'excluded_by_scope')
        self.assertEqual(result['decision'],'manual_review')
        self.assertTrue(result['scope_policy_decision']['excluded_from_main_graph'])
        self.assertEqual(self.fixture.repo.get_candidate(c['candidate_id'])['workflow_state'],'excluded_by_scope')

    def test_core_supported_expression_fail_cannot_count_as_complete_support(self):
        _,result,_=self.run_review(expression='needs_revision')
        self.assertEqual(result['source_support_result'],'unresolved')
        self.assertEqual(result['workflow_state'],'revision_required')
        self.assertEqual(result['consensus']['source_support_consensus']['support_votes'],0)
        self.assertEqual(result['consensus']['core_source_support_result'],'supported')

    def test_shared_admission_checks_scope_from_bound_assessments_even_without_route_flag(self):
        from review_agent.admission import validate_admission
        f=self.fixture;c=copy.deepcopy(f.candidate);bundle=verify_evidence_bundle(make_evidence_bundle(c['evidence_items'],[]),f.index,c)
        c['triple']['relation_id']='R_old'
        assessments=[{'evidence_id':x['evidence_id'],'role':'support','knowledge_scope':'local_definition','expression_status':'faithful'} for x in bundle['items']]
        decision={'candidate_id':c['candidate_id'],'revision':1,'decision':'human_accept','source':'human_confirmed','human_bundle_id':'bound'}
        result=validate_admission(c,decision,f.base,f.index,bundle,assessments,'human')
        self.assertFalse(result.allowed);self.assertIn('EXCLUDED_BY_SCOPE',result.reason_codes)


if __name__=='__main__': unittest.main()
