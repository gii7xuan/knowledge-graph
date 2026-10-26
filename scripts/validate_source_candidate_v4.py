"""Save a reviewable source/candidate v4 fixture run without any network calls."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from document.hierarchy_index import load_document_index
from evidence.verification import make_evidence_bundle, verify_evidence_bundle
from ontologyBuild.providers import LegacyOntologyProvider, OntologyImportRequest
from ontologyBuild.release_service import publish_release
from review_agent.repository import ReviewRepository


ROOT = Path(__file__).resolve().parents[1]


def qualifier_values(polarity="positive", condition=None, modality="asserted"):
    return {"polarity": polarity, "condition": condition, "time_scope": None,
            "applicability": None, "modality": modality, "quantifier": None}


def main():
    from extract_triplets import extrct
    from extract_triplets.pipeline_v4 import run_v4_extraction
    output = ROOT / "runs" / "source_v4_validation" / uuid4().hex
    output.mkdir(parents=True)
    source = output / "source.md"
    source.write_bytes(("# 说明\n离线样例保留[原始链接](https://example.org)和 😀。\n"
                        "## 校准\n完成校准后，传感器A才能测量湿度。\n"
                        "## 校准\n完成校准后，传感器A才能测量湿度。\n"
                        "## 限制\n传感器A不能测量温度。\n"
                        "## 周期\n传感器A的采样周期为2秒。\n"
                        "## 未覆盖关系\n传感器A支持远程自检。\n").replace("\n", "\r\n").encode("utf-8"))
    schema = {"domain": "source_validation_fixture", "classes": {"Device": {"label": "设备"}, "Quantity": {"label": "物理量"}},
              "object_properties": {"measures": {"id": "measures", "label": "测量", "domain": ["Device"], "range": ["Quantity"]}},
              "data_properties": {"period": {"id": "period", "label": "采样周期", "domain": ["Device"], "datatype": "integer"}}}
    schema_path = output / "fixture_ontology.json"
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    release = publish_release(LegacyOntologyProvider().build_or_import(OntologyImportRequest(schema_path)), output / "fixture_releases",
                              approval={"status": "approved", "approved_by": "offline_fixture", "reason": "synthetic test only"})

    class FixtureCompletions:
        def __init__(self): self.chat = SimpleNamespace(completions=self)
        def create(self, **request):
            payload = json.loads(request["messages"][1]["content"])
            core, candidates = payload["core_text"], []
            for quote, relation, tail, values in (
                ("完成校准后，传感器A才能测量湿度。", "测量", "湿度", qualifier_values(condition="完成校准", modality="can")),
                ("传感器A不能测量温度。", "测量", "温度", qualifier_values(polarity="negative")),
                ("传感器A的采样周期为2秒。", "采样周期", "2", qualifier_values()),
                ("传感器A支持远程自检。", "支持", "远程自检", qualifier_values()),
            ):
                if quote not in core: continue
                raw = {"head": "传感器A", "surface_relation": relation, "tail": tail,
                       "evidence_quotes": [quote], "qualifiers": values,
                       "qualifier_status": {key: "extracted" if value is not None else "not_stated" for key, value in values.items()}}
                if relation == "采样周期": raw.update(tail_kind="literal", value=2, datatype="integer", unit="s", language=None)
                candidates.append(raw)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"triplets": candidates,
                "coverage_status": "complete_for_supplied_scope"}, ensure_ascii=False)), finish_reason="stop")],
                model="fixed_fixture_not_a_live_model", id="fixture_response", usage=None)

    with patch.multiple(extrct, project_root=str(output), MARKDOWN_FILE_PATH=str(source), ONTOLOGY_RELEASE=release,
                        REVIEW_CONFIG={}, REVIEW_DATABASE_PATH=str(output / "review.sqlite3"), client=FixtureCompletions(),
                        MODEL="fixed_fixture_not_a_live_model", OUTPUT_JSON_PATH=str(output / "accepted.json"),
                        DISCARDED_TRIPLETS_PATH=str(output / "rejected.json"), EVIDENCE_MD_PATH=str(output / "evidence.md"),
                        RUNS_V2_DIR=str(output / "compatibility_exports")), patch.dict(os.environ, {"KG_RUN_ID": "source_fixture_v4"}):
        if run_v4_extraction(SimpleNamespace(max_chunks=None), extrct) != 0:
            raise AssertionError("fixture extraction incomplete")
    repository = ReviewRepository(output / "review.sqlite3")
    manifest = repository.get_run("source_fixture_v4")
    index = load_document_index(manifest["document_path"], manifest["source_index_hash"], require_verified=True)
    candidates = repository.list_candidates("source_fixture_v4", limit=100)
    assert len(candidates) == 5
    conditional = [candidate for candidate in candidates if candidate["triple"]["qualifiers"].get("condition")]
    assert len(conditional) == 2 and conditional[0]["fact_key"] == conditional[1]["fact_key"]
    for candidate in candidates:
        assert verify_evidence_bundle(make_evidence_bundle(candidate["evidence_items"], []), index, candidate)["items"]
    extraction = json.loads(Path(manifest["extraction_report_path"]).read_text(encoding="utf-8"))
    report = {"mode": "fixed_fixture_no_network", "output_path": str(output), "manifest": str(output / "runs/v3/source_fixture_v4/manifest.json"),
              "candidate_count": len(candidates), "duplicate_fact_sources_retained": 2,
              "conditional_and_modal_candidate_count": 2, "negative_candidate_count": 1,
              "typed_literal_candidate_count": 1, "unmapped_candidate_count": 1,
              "source_integrity": "verified", "all_anchors_verified": True,
              "live_model_calls": 0, "neo4j_connections": 0, "accuracy_evaluated": False,
              "intake_metrics": extraction["intake_metrics"]}
    (output / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
