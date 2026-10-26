"""Import ontology artifacts; publication requires an explicit approval action."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from ontologyBuild.providers import LegacyOntologyProvider, NeonImportProvider, OntologyImportRequest
from ontologyBuild.release_service import artifact_files, digest, publish_release, validation_report
from ontologyBuild.application_profile import quarantine_legacy_artifact
from ontologyBuild.release_catalog import ReleaseCatalog

ROOT = Path(__file__).resolve().parents[1]


def import_artifact(request, provider="auto"):
    if provider == "auto":
        provider = "legacy" if request.source.suffix.lower() == ".json" else "neon"
    return (LegacyOntologyProvider() if provider == "legacy" else NeonImportProvider()).build_or_import(request)


def main(argv=None):
    parser = argparse.ArgumentParser(description="导入 TTL/JSON 制品，校验并可显式发布本体 release")
    parser.add_argument("--source", required=True)
    parser.add_argument("--provider", choices=["auto", "neon", "legacy"], default="auto")
    parser.add_argument("--method-manifest", help="作者工具 commit、模型、参数等来源记录 JSON")
    parser.add_argument("--validation-report", help="作者方法/外部工具的实际校验报告 JSON")
    parser.add_argument("--requirements", help="领域需求 JSON")
    parser.add_argument("--competency-questions", help="能力问题 JSON")
    parser.add_argument("--draft-output", help="新的导入检查目录")
    parser.add_argument("--publish", action="store_true", help="显式批准并发布，不修改默认本体")
    parser.add_argument("--approved-by")
    parser.add_argument("--approval-reason")
    parser.add_argument("--approved-event-id")
    parser.add_argument("--quarantine-invalid", action="store_true", help="创建保留来源的受限 legacy profile，隔离冲突属性和未声明子类")
    parser.add_argument("--confirm-profile-differences", action="store_true", help="显式确认本次 profile 与原始制品的差异")
    parser.add_argument("--activate", action="store_true", help="发布并比较更新当前版本")
    parser.add_argument("--expected-base-release-id", help="更新当前版本时必须匹配的基线；首次发布留空")
    parser.add_argument("--release-root", default=str(ROOT / "runs" / "ontology_releases"))
    args = parser.parse_args(argv)
    if args.publish and (not args.approved_by or not args.approval_reason):
        parser.error("--publish requires --approved-by and --approval-reason")
    if args.activate and not args.publish:
        parser.error("--activate requires --publish")

    def read(path, default):
        return json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else default

    request = OntologyImportRequest(
        source=Path(args.source), method_manifest=read(args.method_manifest, {}),
        validation_report=read(args.validation_report, {}), requirements=read(args.requirements, []),
        competency_questions=read(args.competency_questions, []),
    )
    artifact = import_artifact(request, args.provider)
    if args.quarantine_invalid:
        artifact = quarantine_legacy_artifact(artifact)
    draft = Path(args.draft_output) if args.draft_output else ROOT / "runs" / "ontology_imports" / uuid4().hex
    draft.mkdir(parents=True, exist_ok=False)
    for name, content in artifact_files(artifact, {"status": "pending"}).items():
        (draft / name).write_bytes(content)
    report = validation_report(artifact)
    result = {"status": "imported_pending_approval", "draft_path": str(draft.resolve()),
              "valid": report["project_validation"]["valid"],
              "auto_review_allowed": report["auto_review_allowed"],
              "ontology_version": digest(artifact.project_schema), "release_id": None}
    (draft / "import_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.publish:
        approval = {
            "status": "approved", "approved_by": args.approved_by, "reason": args.approval_reason,
            "approved_event_id": args.approved_event_id, "ontology_version": result["ontology_version"],
        }
        if args.confirm_profile_differences:
            approval.update({"confirmed_profile_hash": digest(artifact.application_profile),
                             "confirmed_mapping_report_hash": digest(artifact.mapping_report)})
        release = (ReleaseCatalog(args.release_root).publish(
            artifact, approval=approval, expected_base_release_id=args.expected_base_release_id)
            if args.activate else publish_release(artifact, args.release_root, approval=approval))
        result.update({"status": "published", **release.binding()})
        result["release_id"] = release.manifest["release_id"]
        result["activated"] = args.activate
        result["base_release_id"] = args.expected_base_release_id if args.activate else None
    (draft / "import_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
