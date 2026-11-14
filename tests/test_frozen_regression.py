"""Frozen population checks and single-launch staging with synthetic identifiers."""
from copy import deepcopy
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from evaluation import frozen_regression as runner
from review_agent.contracts import sha256_json
from runs.regress_v8 import fingerprints


class FrozenPopulationTests(unittest.TestCase):
    def population(self):
        candidates=[{'candidate_id':'sensor-'+str(i),'revision':1,'triple':{'head':'x','tail':str(i)}} for i in range(3)]
        bindings=[{'sample_id':'sample-'+str(i),'candidate_id':c['candidate_id'],'revision':1,
            'candidate_content_hash':sha256_json(c)} for i,c in enumerate(candidates)]
        return {'bindings':bindings,'selected_raw_candidates':candidates}

    def test_exact_population_is_accepted_and_empty_or_truncated_input_rejected(self):
        data=self.population();self.assertEqual(len(runner.validated_population(data)),3)
        for change in ('empty','short'):
            bad=deepcopy(data);bad['selected_raw_candidates']=[] if change=='empty' else bad['selected_raw_candidates'][:-1]
            with self.assertRaises(ValueError):runner.validated_population(bad)

    def test_duplicate_reordered_or_changed_binding_is_rejected(self):
        for change in ('duplicate','reorder','revision','content'):
            bad=self.population()
            if change=='duplicate':bad['bindings'][1]['sample_id']=bad['bindings'][0]['sample_id']
            elif change=='reorder':bad['bindings'].reverse()
            elif change=='revision':bad['bindings'][0]['revision']=2
            else:bad['selected_raw_candidates'][0]['triple']['head']='changed'
            with self.subTest(change=change),self.assertRaises(ValueError):runner.validated_population(bad)

    def test_code_freeze_includes_ontology_review_and_graph_delivery(self):
        files=fingerprints()
        for path in ('ontology_review/validators.py','ontology_review/review_payload.py',
                     'to_neo4j/fact_projection.py','evaluation/frozen_regression.py','runs/finalize_v8_regression.py'):
            self.assertIn(path,files)


class FrozenExecutionTests(unittest.TestCase):
    def setUp(self):
        self.folder=Path(__file__).resolve().parents[1]/'outputs'/('frozen_runner_test_'+uuid4().hex)
        self.folder.mkdir();self.addCleanup(shutil.rmtree,self.folder)
        self.bindings=[{'sample_id':'sample-'+str(i),'candidate_id':'candidate-'+str(i)} for i in range(30)]
        self.frozen={'freeze_hash':'test-freeze','smoke_sample_ids':[b['sample_id'] for b in self.bindings[:7]]}

    def run_frozen(self,pass_gate=True):
        with patch.object(runner,'verify',return_value=(self.frozen,self.bindings)),\
             patch.object(runner,'ReviewRepository') as repository,\
             patch.object(runner,'settings_for',return_value={}),\
             patch.object(runner,'run_review_batch',return_value={}) as batch,\
             patch.object(runner,'summarize',return_value={'unresolved_attempts':0,'requests_settled':True,
                 'engineering_gate_passed':pass_gate}):
            repository.return_value.get_revision.return_value={'run_id':'test-run'}
            result=runner.execute(self.folder)
            calls=[c.kwargs['candidate_ids'] for c in batch.call_args_list]
        return result,calls

    def test_smoke_subset_is_not_repeated_and_second_launch_is_fenced(self):
        result,calls=self.run_frozen()
        self.assertEqual(result['status'],'completed')
        self.assertEqual([len(c) for c in calls],[7,23])
        self.assertEqual(len(set(calls[0]+calls[1])),30)
        with self.assertRaises(FileExistsError):self.run_frozen()

    def test_engineering_failure_prevents_the_remaining_stage(self):
        result,calls=self.run_frozen(pass_gate=False)
        self.assertEqual(result['status'],'stopped');self.assertEqual(len(calls),1)

    def test_changed_freeze_cannot_create_a_launch_receipt(self):
        with patch.object(runner,'verify',side_effect=ValueError('code changed')):
            with self.assertRaises(ValueError):runner.execute(self.folder)
        self.assertFalse((self.folder/'started.json').exists())
