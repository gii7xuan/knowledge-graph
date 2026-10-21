import unittest

from extract_triplets.extrct import v2_bucket_for


class V2RoutingTests(unittest.TestCase):
    def test_manual_review_is_preserved_for_review(self):
        self.assertEqual(v2_bucket_for("manual_review"), "review_queue")

    def test_accept_decisions_are_accepted(self):
        self.assertEqual(v2_bucket_for("auto_accept"), "accepted")
        self.assertEqual(v2_bucket_for("human_accept"), "accepted")

    def test_reject_decision_is_rejected(self):
        self.assertEqual(v2_bucket_for("auto_reject"), "rejected")


if __name__ == "__main__":
    unittest.main()
