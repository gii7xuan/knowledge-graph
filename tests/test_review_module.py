import unittest

from ontologyBuild.review_module import apply_user_decision


class ReviewModuleGuardTests(unittest.TestCase):
    def test_modify_cannot_override_protected_validation_fields(self):
        reviewed = {
            "passed": True,
            "accepted_candidates": [],
            "rejected_candidates": [],
            "pending_review_candidates": [
                {
                    "candidate_id": "c1",
                    "candidate_type": "class",
                    "name": "测试概念",
                    "definition_candidate": "测试概念是指用于验证本体抽取流程的稳定概念。",
                    "status": "new_candidate",
                    "validation_status": "passed",
                    "format_passed": True,
                    "evidence_passed": True,
                    "business_passed": True,
                    "final_decision": "pending_review",
                }
            ],
            "deferred_candidates": [],
            "review_log": [],
            "summary": {},
            "errors": [],
        }
        updated = apply_user_decision(
            reviewed,
            candidate_id="c1",
            user_decision="modify",
            reviewer="tester",
            modifications={"validation_status": "passed", "name": "新名称"},
        )
        self.assertFalse(updated["passed"])
        self.assertIn("禁止修改受保护字段", updated["errors"][-1]["message"])


if __name__ == "__main__":
    unittest.main()
