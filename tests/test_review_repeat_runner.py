"""Offline proof that repeat trials use fresh seats and preserve the original audit."""
import hashlib
import json
import unittest
from unittest.mock import patch
from tests import test_review_four_axes as fixtures
from runs import repeat_dev016_v8_20261109 as repeat


class RepeatRunnerTests(unittest.TestCase):
    def test_exact_frozen_proposal_gets_two_fresh_ballots_without_changing_baseline(self):
        f=fixtures.FourAxisIntegrationTests(methodName='runTest');f.setUp();self.addCleanup(f.doCleanups)
        candidate,result,orchestrator=f.run_case(version='validator_response_v8')
        root=f.fixture.folder/'repeat';root.mkdir()
        (root/'sample_bindings.json').write_text(json.dumps([{'sample_id':'DEV-016','candidate_id':candidate['candidate_id'],
            'run_id':candidate['run_id'],'revision':candidate['revision']}]),encoding='utf-8')
        database=f.fixture.repo.database_path
        before=hashlib.sha256(database.read_bytes()).hexdigest()
        with patch.object(repeat,'OUT',root),patch.object(repeat,'DB',database),patch.object(repeat,'configuration',return_value=orchestrator.config),\
             patch.object(repeat,'public_settings',return_value={'sampling':{'temperature':0.0}}),\
             patch.object(repeat,'_configured_validators',return_value=orchestrator.validators):
            repeat.main()
            report=json.loads((root/'repeat_report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['trials'],3)
            self.assertEqual(report['new_model_calls'],6)
            self.assertTrue(all(v['valid_trials']==3 for v in report['voters']))
            self.assertTrue(all(len(v['client'].calls)==3 for v in orchestrator.validators))
            repeat.main()
            self.assertTrue(all(len(v['client'].calls)==3 for v in orchestrator.validators))
        self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(),before)


if __name__=='__main__':unittest.main()
