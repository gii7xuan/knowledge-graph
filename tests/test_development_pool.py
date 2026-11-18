from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from evaluation.development_pool import curated_paragraphs,excerpt,original_anchors,ordered_sample,bind_source_runs,tags,render_cards
from extract_triplets.audited_requests import judge_once
from extract_triplets.candidate_contract import RESPONSE_SCHEMA
from review_agent.contracts import sha256_json
from review_agent.llm_clients import LLMResponse,MockLLMClient


class SourceSelectionTests(unittest.TestCase):
    def test_source_paragraphs_remain_exact_and_out_of_scope_text_is_not_selected(self):
        text='# 2.1 Ideal Gas Law\n\n'+('A simple gas has equilibrium properties that describe its physical state. '*3)+'\n\n'+('A projectile has a weapon launch application. '*3)+'\n\n# Problem 1\n\n'+('This exercise is never selected. '*5)+'\n\n# 2.2 Other Gas Laws\n'
        accepted,rejected=curated_paragraphs(text,('2.1 ',),'en')
        self.assertEqual(len(accepted),1)
        self.assertEqual(text[accepted[0]['start']:accepted[0]['end']],accepted[0]['text'])
        self.assertTrue(any(x['reason']=='outside_descriptive_concept_scope' for x in rejected))
        self.assertNotIn('exercise',' '.join(x['text'] for x in accepted))

    def test_excerpt_quotes_bind_to_parent_offsets_and_cannot_cross_omissions(self):
        parent='prefix original paragraph one. gap paragraph two. suffix'
        values=['original paragraph one.','paragraph two.'];rows=[]
        for value in values:
            start=parent.index(value);rows.append({'text':value,'start':start,'end':start+len(value),'section':'safe'})
        text,segments=excerpt(rows,'doc');s=segments[0]
        candidate={'evidence_items':[{'evidence_id':'e','start':s['excerpt_start'],'end':s['excerpt_end'],'quote':values[0]}]}
        doc={'segments':segments,'source_group_id':'book','parent_path':'source','parent_document_version':'v'}
        self.assertEqual(original_anchors(candidate,doc,parent)[0]['start'],parent.index(values[0]))
        candidate['evidence_items'][0]['end']=segments[1]['excerpt_end']
        with self.assertRaisesRegex(ValueError,'crosses'):original_anchors(candidate,doc,parent)
        bad=deepcopy(candidate);bad['evidence_items'][0].update(end=s['excerpt_end'],quote='rewritten')
        with self.assertRaisesRegex(ValueError,'mismatch'):original_anchors(bad,doc,parent)

    def test_sampling_is_order_independent_and_does_not_read_predictions_or_labels(self):
        cs=[]
        for i in range(70):
            cs.append({'candidate_id':str(i),'source_group_id':'book'+str(i%2),'source_section':str(i%4),
                'triple':{'relation_id':None},'evidence_quotes':['If this fluid does not move, it is in equilibrium.']})
        a=ordered_sample(cs,40)
        for c in cs:c.update(label=1,prediction='accept',confidence=1)
        b=ordered_sample(list(reversed(cs)),40)
        self.assertEqual([c['candidate_id'] for c in a],[c['candidate_id'] for c in b])
        self.assertEqual(len({c['candidate_id'] for c in a}),40)
        self.assertEqual(len({c['source_group_id'] for c in a}),2)
        self.assertEqual(len(ordered_sample(cs,160)),70)

    def test_document_run_binding_is_audited_and_preserves_every_semantic_field(self):
        original=[{'candidate_id':'old'+str(i),'run_id':'shared-extraction','document_id':'doc'+str(i),
            'contract_version':'extraction_candidate_v4','revision':1,'fact_key_state':'provisional',
            'source_chunk_id':'chunk','triple':{'head':'A','surface_relation':'is','tail':'B'},
            'evidence_items':[{'quote':'A is B.'}],'qualifier_status':{'modality':'unknown'}} for i in range(2)]
        before=deepcopy(original);bound=bind_source_runs(original)
        self.assertEqual(original,before)
        self.assertNotEqual(bound[0]['run_id'],bound[1]['run_id'])
        for a,b in zip(original,bound):
            for key in ('triple','evidence_items','qualifier_status'):self.assertEqual(a[key],b[key])
            self.assertEqual(b['extraction_source_binding']['candidate_content_hash'],sha256_json(a))
            self.assertEqual(b['fact_key'],'provisional:'+b['candidate_id']+':1')


class ExtractionRequestTests(unittest.TestCase):
    def proposal(self):
        p={'task_id':'source','system_prompt':'extract','payload':{'text':'A fluid is matter.'},
            'max_output_tokens':9000,'schema_hash':sha256_json(RESPONSE_SCHEMA)}
        p['proposal_digest']=sha256_json(p);return p

    def voter(self,response):
        return {'id':'extractor','model_id':'mock','model_family':'mock','client':MockLLMClient([response])}

    def test_extraction_result_is_not_a_review_vote_or_human_label(self):
        response={'triplets':[],'coverage_status':'complete_for_supplied_scope'}
        voter=self.voter(response);result=judge_once(voter,self.proposal())
        self.assertEqual(result['response_status'],'ok',result)
        self.assertFalse(result['counts_as_review_vote']);self.assertFalse(result['counts_as_human_label'])
        self.assertEqual(len(voter['client'].calls),1)

    def test_hash_tampering_prevents_dispatch(self):
        voter=self.voter({});p=self.proposal();p['payload']['text']='changed'
        result=judge_once(voter,p)
        self.assertTrue(result['not_sent']);self.assertEqual(len(voter['client'].calls),0)

    def test_truncation_never_becomes_a_valid_extraction(self):
        voter=self.voter({})
        with patch.object(voter['client'],'complete_json',return_value=LLMResponse({'triplets':[],'coverage_status':'complete_for_supplied_scope'},finish_reason='length')):
            result=judge_once(voter,self.proposal())
        self.assertNotEqual(result['response_status'],'ok');self.assertIn('incomplete extraction output',result['validation_errors'])


if __name__=='__main__':unittest.main()
