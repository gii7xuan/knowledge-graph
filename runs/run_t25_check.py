import os
from pathlib import Path
import unittest
from uuid import uuid4
from scripts.isolated_neo4j import isolated_neo4j

with isolated_neo4j(Path('runs/graph_validation')/('t25_'+uuid4().hex)) as graph:
    os.environ['KG_TEST_NEO4J_URI']=graph['uri']
    result=unittest.TextTestRunner(verbosity=2,buffer=True).run(unittest.defaultTestLoader.loadTestsFromName('tests.test_admission_graph_sync.RealGraphTests.test_t25_v4_auto_admission_imports_only_current_round_assessments'))
    if not result.wasSuccessful(): raise SystemExit(1)
