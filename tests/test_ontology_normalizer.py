import unittest

from ontologyBuild.ontology_normalizer import merge_duplicate_candidates


class OntologyNormalizerDeduplicationTests(unittest.TestCase):
    def test_object_properties_keep_distinct_domain_range_pairs(self):
        candidates = {
            "candidate_object_properties": [
                {"name": "包括", "domain_candidate": "火炮", "range_candidate": "身管"},
                {"name": "包括", "domain_candidate": "火炮", "range_candidate": "炮闩"},
                {"name": "包括", "domain_candidate": "火炮", "range_candidate": "身管"},
            ]
        }
        result = merge_duplicate_candidates(candidates)["candidate_object_properties"]
        self.assertEqual(len(result), 2)
        self.assertEqual(
            {(item["domain_candidate"], item["range_candidate"]) for item in result},
            {("火炮", "身管"), ("火炮", "炮闩")},
        )
        self.assertEqual(len(result[0]["merged_duplicates"]), 1)

    def test_datatype_properties_keep_the_same_name_for_different_domains(self):
        candidates = {
            "candidate_datatype_properties": [
                {"name": "速度", "domain_candidate": "弹丸", "range_datatype": "float"},
                {"name": "速度", "domain_candidate": "自动机", "range_datatype": "float"},
            ]
        }
        result = merge_duplicate_candidates(candidates)["candidate_datatype_properties"]
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
