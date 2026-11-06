"""Command-line entry points for the v3 review lifecycle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from confidence.calibrator import TripleCalibrator, load_calibrator
from confidence.risk_policy import RiskPolicy, load_policy
from review_agent.contracts import candidate_id_for, fact_key_for
from review_agent.feedback import FeedbackService
from review_agent.llm_clients import MockLLMClient, OpenAICompatibleClient
from review_agent.memory import ReviewMemory
from review_agent.ontology_guard import OntologyGuard, load_snapshot
from review_agent.orchestrator import ReviewOrchestrator
from review_agent.repository import ReviewRepository, MIGRATION_VERSION
from review_agent.service import ReviewService, load_config
from review_agent.response_contract import CHECK_NAMES
from review_agent.model_settings import validator_settings


ROOT = Path(__file__).resolve().parents[1]


def _database(args: Any, config: Dict[str, Any]) -> Path:
    value = Path(getattr(args, "database", None) or config.get("database_path", "runs/v3/review.sqlite3"))
    return value if value.is_absolute() else ROOT / value


def _document_index(document_id: str, text: str) -> Dict[str, Any]:
    document_version = hashlib.sha256(text.encode("utf-8")).hexdigest()
    chunk_id = f"chunk_{hashlib.sha256((document_id + text).encode('utf-8')).hexdigest()[:12]}"
    section_id = f"section_{hashlib.sha256(document_id.encode('utf-8')).hexdigest()[:12]}"
    sentences = []
    for index, match in enumerate(re.finditer(r"[^。！？!?；;\n]+[。！？!?；;]?", text)):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        start, end = match.start() + leading, match.end() - trailing
        if start == end:
            continue
        sentences.append({
            "sentence_id": f"{chunk_id}_s{index}", "text": text[start:end],
            "start": start, "end": end, "chunk_start": start, "chunk_end": end,
            "document_start": start, "document_end": end,
        })
    chunk = {
        "document_id": document_id, "document_version": document_version,
        "chunk_id": chunk_id, "section_id": section_id, "parent_section_id": None,
        "breadcrumb_path": "设备说明", "chunk_order": 0, "content": text,
        "sentences": sentences, "offset_basis": "canonical_document",
    }
    return {
        "document_id": document_id, "document_version": document_version,
        "chunks": [chunk], "by_chunk_id": {chunk_id: chunk}, "by_section_id": {section_id: [chunk]},
        "children_by_section_id": {}, "descendant_chunks_by_section_id": {section_id: [chunk]},
    }


def _configured_validators(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    validators = []
    for slot in config.get("validators") or []:
        default_timeout=float((config.get('budget') or {}).get('per_call_timeout_seconds',45))
        settings = validator_settings({'request_timeout_seconds':default_timeout,**slot})
        if settings['request_timeout_seconds']<=0 or settings['http_timeout_seconds']<=0 or settings['max_output_tokens']<1:
            raise ValueError('validator timing and output limits must be positive')
        model, family, key, url = (settings[name] for name in ("model", "family", "api_key", "base_url"))
        if not all((model, family, key, url)):
            continue
        validators.append({
            "id": slot["id"], "model_id": model, "model_family": family,
            "model_identity": settings["model_identity"],
            **{k:settings[k] for k in ('request_timeout_seconds','http_timeout_seconds','max_output_tokens')},
            "client": OpenAICompatibleClient(api_key=key, base_url=url, model_id=model, model_family=family,
                                             timeout_seconds=settings['http_timeout_seconds'],
                                             thinking_mode=settings['thinking_mode'],
                                             stream_responses=settings['stream_responses'],
                                             verify_ssl=settings["verify_ssl"]),
        })
    return validators


def command_init(args: Any) -> Dict[str, Any]:
    config = load_config(args.config)
    repository = ReviewRepository(_database(args, config))
    repository.initialize()
    return {"ok": True, "database_path": str(repository.database_path), "migration_version": MIGRATION_VERSION}


def command_export(args: Any) -> Dict[str, Any]:
    config = load_config(args.config)
    repository = ReviewRepository(_database(args, config))
    repository.initialize()
    output = Path(args.output or ROOT / "runs" / "v3" / args.run_id / "exports")
    return ReviewService(repository, config=config).export_run(args.run_id, output)


def command_feedback_dataset(args: Any) -> Dict[str, Any]:
    config = load_config(args.config)
    repository = ReviewRepository(_database(args, config))
    repository.initialize()
    rows = repository.feedback_rows()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "valid_labels": len(rows), "positive": sum(row["label"] == 1 for row in rows),
        "negative": sum(row["label"] == 0 for row in rows),
        "missing_feature_snapshots": sum(not row.get("features") for row in rows),
    }
    report_path = output.with_name(output.stem + "_quality_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": str(output), "report": str(report_path), **report}


def command_reliability(args: Any) -> Dict[str, Any]:
    rows = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    stats: Dict[str, Dict[str, int]] = {}
    for row in rows:
        for vote in row.get("votes") or []:
            if vote.get("response_status") != "ok":
                continue
            voter = str(vote.get("voter_id"))
            value = stats.setdefault(voter, {"correct": 0, "evaluated": 0})
            value["evaluated"] += 1
            predicted = 1 if vote.get("verdict") == "valid" else 0 if vote.get("verdict") == "invalid" else None
            value["correct"] += predicted is not None and predicted == row.get("label")
    output = {voter: {**value, "reliability": (value["correct"] + 2) / (value["evaluated"] + 4)}
              for voter, value in stats.items()}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": str(path), "voters": len(output)}


def command_build_memory(args: Any) -> Dict[str, Any]:
    # Cases are already transactionally published from human events.  This command freezes a portable snapshot.
    config = load_config(args.config)
    repository = ReviewRepository(_database(args, config))
    repository.initialize()
    memory = ReviewMemory(repository)
    snapshot_id = memory.current_version()
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT case_id, memory_version, case_json FROM memory_cases WHERE active=1 AND memory_version<=? ORDER BY memory_version",
            (snapshot_id,),
        ).fetchall()
    cases = [{"case_id": row["case_id"], "memory_version": row["memory_version"],
              **json.loads(row["case_json"])} for row in rows]
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"snapshot_id": snapshot_id, "cases": cases}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": str(path), "snapshot_id": snapshot_id, "cases": len(cases)}


def command_audit(args: Any) -> Dict[str, Any]:
    from .batch import run_review_batch

    config = load_config(args.config)
    if args.mode:
        config["mode"] = args.mode
    repository = ReviewRepository(_database(args, config))
    repository.initialize()
    status_file = getattr(args, "status_file", None) or str(
        ROOT / "runs" / "review_jobs" / ("audit_" + uuid4().hex[:12]) / "status.json")
    previous = None
    from .recovery_service import seed_service_pauses
    seed_service_pauses(repository,_configured_validators(config))
    if getattr(args,'validator_contract',None): config['validator_contract_version']=args.validator_contract

    def progress(state):
        nonlocal previous
        key = (state["status"], state["completed"], state["failed"], state["skipped"])
        if key != previous:
            print(f'[review] status={state["status"]} completed={state["completed"]}/{state["selected"]} '
                  f'skipped={state["skipped"]} failed={state["failed"]}', flush=True)
            previous = key

    print(f"[review] status_file={status_file}", flush=True)
    runner=None
    if (config.get('ontology_extension') or {}).get('enabled') and not getattr(args,'no_consumer',False):
        from .jobs import JobRunner
        service=ReviewService(repository,config=config)
        runner=JobRunner(repository,service.execute_audit_job,max_workers=1)
        service.job_runner=runner
        runner.start()
    try:
        result=run_review_batch(repository, args.run_id, config,
            candidate_ids=getattr(args, "candidate_id", None),
            force=bool(getattr(args, "force", False)), limit=getattr(args, "limit", None),
            status_file=status_file, on_progress=progress)
        return {**result,'queue_consumer_started':runner is not None,
                'queue_worker_command':'python -m review_agent.cli worker'}
    finally:
        if runner: runner.shutdown()


def command_worker(args):
    from .jobs import JobRunner
    config=load_config(args.config)
    repository=ReviewRepository(_database(args,config));repository.initialize()
    from .recovery_service import seed_service_pauses
    seed_service_pauses(repository,_configured_validators(config))
    service=ReviewService(repository,config=config)
    runner=JobRunner(repository,service.execute_audit_job,max_workers=1,job_types=args.job_type)
    service.job_runner=runner
    try:
        if args.once: return runner.run_available_once() or {'status':'idle_or_service_paused'}
        runner.serve()
    except KeyboardInterrupt:
        return {'status':'worker_stopped'}
    finally: runner.shutdown()


def command_probe_services(args):
    from .recovery_service import probe_services
    config=load_config(args.config)
    repository=ReviewRepository(_database(args,config));repository.initialize()
    return probe_services(repository,_configured_validators(config))


def command_recover_audit(args):
    from .recovery_service import recover_frozen_audit,seed_service_pauses
    config=load_config(args.config)
    repository=ReviewRepository(_database(args,config));repository.initialize()
    seed_service_pauses(repository,_configured_validators(config))
    candidate=repository.get_candidate(args.candidate_id)
    manifest=repository.get_run(candidate['run_id'])
    guard=OntologyGuard(load_snapshot(candidate.get('ontology_path') or manifest['ontology_path']))
    orchestrator=ReviewOrchestrator(repository,guard,_configured_validators(config),config=config)
    return recover_frozen_audit(repository,orchestrator,args.candidate_id,args.audit_id,reason=args.reason,
        extra_calls=args.extra_calls,extra_tokens=args.extra_tokens,extra_seconds=args.extra_seconds)


def command_revise_candidate(args):
    from .recovery_service import seed_service_pauses
    from .request_execution import service_key
    from .model_settings import create_planner_client
    from document.hierarchy_index import load_document_index
    config=load_config(args.config)
    repository=ReviewRepository(_database(args,config));repository.initialize()
    voters=_configured_validators(config)
    seed_service_pauses(repository,voters)
    if any(repository.service_pause(service_key(v)) for v in voters):
        return {'status':'service_paused','no_work_started':True}
    candidate=repository.get_candidate(args.candidate_id)
    manifest=repository.get_run(candidate['run_id'])
    document=load_document_index(manifest['document_path'],candidate.get('source_index_hash'),require_verified=True)
    guard=OntologyGuard(load_snapshot(candidate.get('ontology_path') or manifest['ontology_path']))
    orchestrator=ReviewOrchestrator(repository,guard,voters,config=config,planner_client=create_planner_client(config))
    return orchestrator.audit_candidate(candidate['candidate_id'],candidate['revision'],document,
                                         context={'revision_only':True,'revision_investigation_reason':args.reason})


def command_resume_jobs(args):
    config=load_config(args.config)
    repository=ReviewRepository(_database(args,config));repository.initialize()
    with repository.connect() as c:
        rows=c.execute("SELECT job_id FROM jobs WHERE status='paused' AND job_type=? AND cancel_requested=0",(args.job_type,)).fetchall()
    return {'resumed':[repository.resume_job(r[0],args.reason)['job_id'] for r in rows],
            'worker_must_be_started_explicitly':True,'existing_costs_preserved':True}


def command_demo(args: Any) -> Dict[str, Any]:
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    database = Path(args.database or ROOT / "runs" / "v3" / "offline_demo.sqlite3")
    repository = ReviewRepository(database)
    repository.initialize()
    run_id = "demo_" + uuid4().hex[:12]
    from copy import deepcopy
    from document.hierarchy_index import build_document_index, split_markdown_text, persist_document_index
    from ontologyBuild.release_catalog import ReleaseCatalog
    from ontologyBuild.providers.base import OntologyArtifact
    from extract_triplets.candidate_contract import normalize_candidate
    from extract_triplets.pipeline_v4 import source_scope
    folder = database.parent / run_id
    folder.mkdir(parents=True)
    index = build_document_index(split_markdown_text(fixture["document"], "sensor_manual", 600, 20))
    document_path = persist_document_index(index, folder / "document.json")
    ontology = deepcopy(fixture["ontology"])
    ontology["application_profile"] = {"profile_version":"project_json_v1", "constraint_semantics":"allowed_any", "auto_review_blocked":False}
    release = ReleaseCatalog(folder / "releases").publish(OntologyArtifact(ontology, {"method":"offline_fixture"}, {},
        {"errors":[],"unsupported_axioms":[]},ontology["application_profile"],source_json=deepcopy(ontology)),
        approval={"status":"approved","approved_by":"offline_fixture_operator","reason":"Demo schema, not production publication"})
    snapshot = load_snapshot(release.snapshot_path)
    repository.create_run({
        "run_id": run_id, "schema_version": "triple_review_v3", "mode": "offline_demo",
        "source_hash": index["document_version"], "ontology_hash": snapshot.ontology_version,
        "document_path": str(document_path), "ontology_path":str(release.snapshot_path), "source_index_hash":index["index_content_hash"],
    })
    source_chunk_id = index["chunks"][0]["chunk_id"]
    candidates = []
    for ordinal, scenario in enumerate(fixture["scenarios"]):
        qualifiers = {"polarity":"positive","condition":None,"time_scope":None,"applicability":None,"modality":"asserted","quantifier":None,**scenario["triple"]["qualifiers"]}
        if scenario["id"] == "correct": qualifiers["modality"] = "can"
        raw = {**scenario["triple"],"evidence_quotes":[scenario["quote"]],"qualifiers":qualifiers,
            "qualifier_status":{k:"extracted" if v is not None else "not_stated" for k,v in qualifiers.items()}}
        candidate = normalize_candidate(raw,index=index,scope=source_scope(index,index["chunks"][0]),ontology=release.ontology,
            release_binding=release.binding(),run_id=run_id,ordinal=ordinal,prompt_hash="offline_fixture")
        candidate["scenario"] = scenario["id"]
        candidate["triple"].update({k:scenario["triple"].get(k) for k in ("head_type","tail_type")})
        candidate["fact_key"] = fact_key_for(candidate)
        repository.add_candidate(candidate)
        candidates.append(candidate)

    scenarios_by_candidate = {item["candidate_id"]: item["scenario"] for item in candidates}

    def response_for(voter_index: int):
        def respond(payload):
            scenario = scenarios_by_candidate.get(payload["candidate"]["candidate_id"])
            evidence_id = payload["evidence"][0]["evidence_id"] if payload.get("evidence") else None
            if scenario == "correct" or (scenario == "counterevidence" and voter_index < 3):
                verdict, support, reasons = "valid", "supported", []
            elif scenario == "counterevidence" and voter_index == 3:
                verdict, support, reasons = "invalid", "contradicted", ["NEGATION_IGNORED"]
            elif scenario in {"negation_error", "direction_error"}:
                verdict, support = "invalid", "contradicted"
                reasons = ["NEGATION_IGNORED" if scenario == "negation_error" else "DIRECTION_MISMATCH"]
            else:
                verdict, support, reasons = "uncertain", "insufficient", ["SCHEMA_GAP"]
            return {
                "verdict": verdict, "source_support": support,
                "schema_status": "unknown" if scenario == "schema_gap" else "pass",
                "schema_issue": "not_covered" if scenario == "schema_gap" else "none", "schema_change_hint": None,
                "reason_codes": reasons,
                "checks": [{"check": name, "status": ("unknown" if scenario == "schema_gap" else "pass")
                            if name == "ontology" else ("pass" if verdict == "valid" else "unknown"),
                            "evidence_ids": [evidence_id] if evidence_id else [], "note": "offline fixture"}
                           for name in CHECK_NAMES],
                "suggested_revision": None,
                "supporting_evidence_ids": [evidence_id] if verdict == "valid" and evidence_id else [],
                "counter_evidence_ids": [evidence_id] if verdict == "invalid" and evidence_id else [],
                "requested_context": {"scope": "same_section", "query": "限定条件", "limit": 4}
                if scenario == "counterevidence" else None,
            }
        return respond

    validators = [{
        "id": f"judge_{letter}", "model_id": f"mock-{letter}", "model_family": f"family-{letter}",
        "client": MockLLMClient(response_for(index_), model_id=f"mock-{letter}", model_family=f"family-{letter}"),
    } for index_, letter in enumerate("abcd")]
    config = {"mode": "offline_demo", "consensus": {"committee_size": 4, "required_votes": 3,
              "minimum_distinct_families": 3, "max_rounds": 2}, "budget": {"max_llm_calls_per_candidate": 12}}
    orchestrator = ReviewOrchestrator(repository, OntologyGuard(snapshot), validators, config=config)
    audit_results = [orchestrator.audit_candidate(item["candidate_id"], 1, index) for item in candidates]

    feedback = FeedbackService(repository, ontology_guard=OntologyGuard(snapshot))
    correct = repository.get_candidate(candidates[0]["candidate_id"])
    accepted = feedback.submit({
        "candidate_id": correct["candidate_id"], "revision": 1,
        "expected_row_version": correct["row_version"], "idempotency_key": f"demo-accept-{run_id}",
        "action": "accept", "reason_code": "HUMAN_CONFIRMED", "labels": {"valid_for_kg": 1},
        "spans":[{"quote":fixture["scenarios"][0]["quote"],"role":"support","note":"Offline fixture operator confirms capability and explicit source statement."}],
    })
    wrong = repository.get_candidate(candidates[1]["candidate_id"])
    rejected = feedback.submit({
        "candidate_id": wrong["candidate_id"], "revision": 1,
        "expected_row_version": wrong["row_version"], "idempotency_key": f"demo-reject-{run_id}",
        "action": "reject", "reason_code": "NEGATION_IGNORED", "labels": {"valid_for_kg": 0},
    })
    mod_source = repository.get_candidate(candidates[-1]["candidate_id"])
    modified = feedback.submit({
        "candidate_id": mod_source["candidate_id"], "revision": 1,
        "expected_row_version": mod_source["row_version"], "idempotency_key": f"demo-modify-{run_id}",
        "action": "modify", "reason_code": "REVISION_REQUIRED", "labels": {"valid_for_kg": None},
        "modifications": {"triple": {"tail": "温度"}},
    })
    duplicate = feedback.submit({
        "candidate_id": correct["candidate_id"], "revision": 1,
        "expected_row_version": correct["row_version"], "idempotency_key": f"demo-accept-{run_id}",
        "action": "accept", "reason_code": "HUMAN_CONFIRMED", "labels": {"valid_for_kg": 1},
        "spans":[{"quote":fixture["scenarios"][0]["quote"],"role":"support","note":"Offline fixture operator confirms capability and explicit source statement."}],
    })
    return {
        "run_id": run_id, "database_path": str(database),
        "audits": [{
            "audit_id": item["audit_id"], "candidate_id": item["candidate_id"],
            "decision": item["decision"], "consensus": item["consensus"]["result"],
            "reason_codes": item["reason_codes"],
        } for item in audit_results],
        "checks": {
            "correct_human_accept": accepted["event"]["action"] == "accept",
            "negation_human_reject": rejected["event"]["action"] == "reject",
            "schema_gap_manual": next(value for value in audit_results if repository.get_revision(value["candidate_id"])["scenario"] == "schema_gap")["decision"] == "manual_review",
            "counterevidence_manual": next(value for value in audit_results if repository.get_revision(value["candidate_id"])["scenario"] == "counterevidence")["decision"] == "manual_review",
            "modified_revision": modified["candidate"]["revision"] == 2,
            "idempotent_feedback": duplicate["event"]["event_id"] == accepted["event"]["event_id"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m review_agent.cli")
    parser.add_argument("--config", default=str(ROOT / "config" / "review_agent.json"))
    parser.add_argument("--database")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    audit = sub.add_parser("audit")
    audit.add_argument("--run-id", required=True)
    audit.add_argument("--mode", choices=["legacy", "assist_only", "shadow", "active", "offline_demo"])
    audit.add_argument("--candidate-id", action="append", help="only review these candidate IDs")
    audit.add_argument("--limit", type=int, help="maximum candidates after healthy checkpoints are skipped")
    audit.add_argument("--force", action="store_true", help="also re-audit healthy completed revisions")
    audit.add_argument("--status-file", help="atomic live progress JSON")
    audit.add_argument('--no-consumer',action='store_true',help='run triple batch only; use separate worker command for durable queue')
    audit.add_argument('--validator-contract',choices=['validator_response_v3','validator_response_v4','validator_response_v5','validator_response_v5_1','validator_response_v5_2','validator_response_v6','validator_response_v6_1','validator_response_v6_2','validator_response_v6_3'])
    worker_parser=sub.add_parser('worker')
    worker_parser.add_argument('--job-type',action='append',choices=['audit','ontology_extension'])
    worker_parser.add_argument('--once',action='store_true')
    sub.add_parser('probe-services',help='explicit same-model availability probe without document content')
    recovery=sub.add_parser('recover-audit',help='recover original frozen failed slots; keep valid votes and unknown requests')
    recovery.add_argument('--candidate-id',required=True);recovery.add_argument('--audit-id',required=True)
    recovery.add_argument('--reason',required=True);recovery.add_argument('--extra-calls',required=True,type=int)
    recovery.add_argument('--extra-tokens',required=True,type=int);recovery.add_argument('--extra-seconds',required=True,type=float)
    revision=sub.add_parser('revise-candidate',help='investigate a reviewed expression without rerolling its original committee')
    revision.add_argument('--candidate-id',required=True);revision.add_argument('--reason',required=True)
    resume=sub.add_parser('resume-jobs',help='explicitly requeue paused work after same-model service probes')
    resume.add_argument('--job-type',choices=['audit','ontology_extension'],required=True);resume.add_argument('--reason',required=True)
    export = sub.add_parser("export")
    export.add_argument("--run-id", required=True)
    export.add_argument("--output")
    dataset = sub.add_parser("build-feedback-dataset")
    dataset.add_argument("--output", required=True)
    memory = sub.add_parser("build-memory")
    memory.add_argument("--dataset")
    memory.add_argument("--output", required=True)
    reliability = sub.add_parser("update-reliability")
    reliability.add_argument("--dataset", required=True)
    reliability.add_argument("--output", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--fixture", required=True)
    demo.add_argument("--database")
    args = parser.parse_args()
    commands = {
        "init-db": command_init, "audit": command_audit, "export": command_export,
        "build-feedback-dataset": command_feedback_dataset, "build-memory": command_build_memory,
        "update-reliability": command_reliability, "demo": command_demo,
        'worker':command_worker,
        'probe-services':command_probe_services,'recover-audit':command_recover_audit,
        'revise-candidate':command_revise_candidate,'resume-jobs':command_resume_jobs,
    }
    result = commands[args.command](args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
