import unittest

from ontologyBuild.candidate_quality import is_fragment_name
from ontologyBuild.directory_guided_builder import (
    build_module_index,
    clean_term,
    generate_candidates,
    phrase_candidates,
    quantity_candidates,
    relation_candidates,
)


class DirectoryGuidedBuilderQualityTests(unittest.TestCase):
    def test_clean_term_removes_markdown_numbering_and_page_number(self):
        self.assertEqual(clean_term("# 1.2 火炮动力学方法 42"), "火炮动力学方法")
        self.assertEqual(clean_term("## 第3章 自动机动力学"), "自动机动力学")

    def test_quality_check_rejects_markup_formula_and_generic_names(self):
        self.assertTrue(is_fragment_name("#火炮动力学"))
        self.assertTrue(is_fragment_name("$q_j$——广义坐标"))
        self.assertTrue(is_fragment_name("方法"))

    def test_quantities_require_an_explicit_domain(self):
        self.assertEqual(
            list(quantity_candidates("弹丸的飞行速度影响射程。")),
            [("飞行速度", "弹丸")],
        )
        self.assertEqual(list(quantity_candidates("式中 $q_j$ 为广义坐标。")), [])
        self.assertEqual(list(quantity_candidates("初始速度。")), [])

    def test_body_classes_come_from_semantic_cues_not_sentence_suffixes(self):
        self.assertEqual(
            list(phrase_candidates("火炮与自动武器动力学是一门应用学科。")),
            ["火炮与自动武器动力学"],
        )
        self.assertEqual(
            list(phrase_candidates("发射下一发弹动作的火炮。")),
            [],
        )
        self.assertEqual(
            list(phrase_candidates("材料的应变随时间变化的特性称为黏性。")),
            ["黏性"],
        )

    def test_relation_candidates_keep_only_compact_entity_endpoints(self):
        self.assertEqual(
            list(relation_candidates("机枪模态决定了机枪的振动特性。")),
            [("决定", "机枪模态", "机枪的振动特性")],
        )
        self.assertEqual(
            list(relation_candidates("现代火炮由身管、膛口装置和反后坐装置组成。")),
            [
                ("由...组成", "现代火炮", "身管"),
                ("由...组成", "现代火炮", "膛口装置"),
                ("由...组成", "现代火炮", "反后坐装置"),
            ],
        )
        self.assertEqual(
            list(relation_candidates("并在很大程度上决定着国防科技成果的转化。")),
            [],
        )
        self.assertEqual(
            list(relation_candidates("模型运动参数包括弹丸速度和射击频率。")),
            [],
        )

    def test_generation_does_not_treat_markdown_header_as_body_candidate(self):
        chunk = {
            "chunk_id": "chunk_1",
            "section_id": "section_1",
            "parent_section_id": None,
            "breadcrumb_path": "火炮与自动武器动力学",
            "content": "# 火炮与自动武器动力学\n现代火炮由身管和反后坐装置组成。",
            "sentences": [
                {"text": "# 火炮与自动武器动力学"},
                {"text": "现代火炮由身管和反后坐装置组成。"},
            ],
        }
        result = generate_candidates([chunk], build_module_index([chunk]))
        names = [item["name"] for item in result["candidate_classes"]]
        self.assertEqual(names.count("火炮与自动武器动力学"), 1)
        self.assertFalse(any(name.startswith("#") for name in names))
        relations = result["candidate_object_properties"]
        self.assertEqual(
            [(item["name"], item["domain_candidate"], item["range_candidate"]) for item in relations],
            [
                ("由...组成", "现代火炮", "身管"),
                ("由...组成", "现代火炮", "反后坐装置"),
            ],
        )
        self.assertEqual(result["candidate_constraints"], [])


if __name__ == "__main__":
    unittest.main()
