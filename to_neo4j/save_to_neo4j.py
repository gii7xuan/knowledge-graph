"""Transactional Neo4j projection of current, authoritative v3 decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from ontologyBuild.ontology_adapter import iter_object_properties
from review_agent.contracts import fact_key_for
from review_agent.ontology_guard import load_snapshot
from review_agent.repository import NotFoundError, ReviewRepository


ROOT = Path(__file__).resolve().parents[1]


def relation_type_map(ontology_path: Any) -> Dict[str, str]:
    ontology = json.loads(Path(ontology_path).read_text(encoding="utf-8"))
    result = {}
    for item in iter_object_properties(ontology):
        relation_id = str(item.get("id") or "").strip()
        if not relation_id:
            continue
        safe = re.sub(r"[^A-Za-z0-9_]", "_", relation_id).strip("_")
        if not safe or not re.match(r"^[A-Za-z_]", safe):
            safe = "R_" + safe
        result[relation_id] = safe
    return result


class Neo4jImporter:
    def __init__(
        self, uri: str, user: str, password: str, *,
        relation_types: Optional[Mapping[str, str]] = None, create_constraints: bool = True,
    ):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.relation_types = dict(relation_types or {})
        if create_constraints:
            self._create_constraints()

    def _create_constraints(self) -> None:
        statements = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_key IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (f:Fact) REQUIRE f.fact_key IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Evidence) REQUIRE e.evidence_key IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_key IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Section) REQUIRE s.section_key IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (d:Document) REQUIRE d.document_key IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (r:ReviewDecision) REQUIRE r.decision_id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (c:CandidateContribution) REQUIRE c.contribution_key IS UNIQUE",
        ]
        with self.driver.session() as session:
            for statement in statements:
                session.run(statement)

    def close(self) -> None:
        self.driver.close()

    @staticmethod
    def normalize_entity_name(name: Any) -> str:
        return "".join(str(name or "").split()).casefold()

    @classmethod
    def make_entity_key(cls, name: Any, domain: str = "default") -> str:
        return hashlib.sha256(f"{domain}\n{cls.normalize_entity_name(name)}".encode("utf-8")).hexdigest()

    @staticmethod
    def make_evidence_key(fact_key: str, item: Mapping[str, Any]) -> str:
        raw = "\n".join(str(item.get(key) or "") for key in (
            "document_id", "document_version", "chunk_id", "sentence_id", "start", "end", "quote", "text"
        ))
        return hashlib.sha256(f"{fact_key}\n{raw}".encode("utf-8")).hexdigest()

    def is_importable(self, item: Mapping[str, Any]) -> bool:
        """Reject legacy flags; only a matching active v3 decision grants admission."""
        decision = item.get("latest_decision")
        if not isinstance(decision, Mapping):
            return False
        if decision.get("active") is False:
            return False
        if int(decision.get("revision") or -1) != int(item.get("revision") or -2):
            return False
        final = decision.get("decision") or decision.get("final_decision")
        if final == "human_accept":
            return bool(decision.get("source") == "human_confirmed" and decision.get("review_event_id"))
        if final == "auto_accept":
            consensus = decision.get("consensus") or {}
            return bool(
                decision.get("audit_id")
                and consensus.get("result") == "accept_consensus"
                and decision.get("calibrated_probability") is not None
                and decision.get("calibration_status") == "ok"
                and decision.get("policy_version")
                and not str(decision.get("policy_version")).startswith("disabled")
            )
        return False

    def _relation_type(self, relation_id: str) -> str:
        if relation_id not in self.relation_types:
            raise ValueError(f"relation_id is not in frozen ontology whitelist: {relation_id}")
        return self.relation_types[relation_id]

    def _prepare(self, item: Mapping[str, Any]) -> Dict[str, Any]:
        if item.get("contract_version") == "extraction_candidate_v4" and item.get("fact_key_state") != "canonical":
            raise ValueError("provisional candidate v4 cannot enter Fact aggregation")
        triple = item.get("triple") if isinstance(item.get("triple"), Mapping) else item
        if item.get("contract_version") == "extraction_candidate_v4":
            qualifiers = triple.get("qualifiers") or {}
            if (qualifiers.get("polarity") != "positive" or any(qualifiers.get(k) for k in ("condition", "time_scope", "applicability"))
                    or qualifiers.get("modality") not in {None, "asserted"}
                    or (triple.get("qualifier_status") or {}).get("quantifier") == "extracted"):
                raise ValueError("qualified candidate v4 requires the full Fact projection adapter")
        head = str(triple.get("head") or "").strip()
        tail = str(triple.get("tail") or "").strip()
        relation_id = str(triple.get("relation_id") or "").strip()
        if not head or not tail or not relation_id:
            raise ValueError("candidate has empty head, relation_id, or tail")
        if str(triple.get("tail_kind") or "entity") != "entity":
            raise ValueError("literal import requires an explicit datatype adapter")
        decision = dict(item["latest_decision"])
        fact_key = str(item.get("fact_key") or fact_key_for(item))
        return {
            "head": head, "tail": tail,
            "head_key": triple.get("head_entity_id") if item.get("contract_version") == "extraction_candidate_v4" else self.make_entity_key(head, str(item.get("domain") or "default")),
            "tail_key": triple.get("tail_entity_id") if item.get("contract_version") == "extraction_candidate_v4" else self.make_entity_key(tail, str(item.get("domain") or "default")),
            "relation_id": relation_id, "relation_type": self._relation_type(relation_id),
            "relation_label": triple.get("relation_label") or triple.get("surface_relation") or relation_id,
            "qualifiers_json": json.dumps(triple.get("qualifiers") or {}, ensure_ascii=False, sort_keys=True),
            "fact_key": fact_key, "candidate_id": item["candidate_id"], "revision": int(item["revision"]),
            "decision_id": decision["decision_id"], "decision_version": int(decision["decision_version"]),
            "audit_id": decision.get("audit_id"), "ontology_version": item.get("ontology_version"),
            "document_version": item.get("document_version"),
            "contribution_key": f"{item['candidate_id']}:{item['revision']}",
        }

    @staticmethod
    def _import_transaction(tx: Any, values: Mapping[str, Any], item: Mapping[str, Any]) -> None:
        relation_type = values["relation_type"]
        tx.run(f"""
            MERGE (h:Entity {{entity_key: $head_key}})
            SET h.name=$head, h.updated_at=datetime()
            MERGE (t:Entity {{entity_key: $tail_key}})
            SET t.name=$tail, t.updated_at=datetime()
            MERGE (f:Fact {{fact_key: $fact_key}})
            ON CREATE SET f.created_at=datetime()
            SET f.head=$head, f.tail=$tail, f.relation_id=$relation_id,
                f.relation_label=$relation_label, f.qualifiers_json=$qualifiers_json,
                f.active=true, f.applied_decision_version=$decision_version,
                f.ontology_version=$ontology_version, f.updated_at=datetime()
            MERGE (h)-[:FACT_SUBJECT]->(f)
            MERGE (f)-[:FACT_OBJECT]->(t)
            MERGE (h)-[edge:{relation_type} {{fact_key: $fact_key}}]->(t)
            SET edge.active=true, edge.qualifiers_json=$qualifiers_json,
                edge.applied_decision_version=$decision_version, edge.updated_at=datetime()
            MERGE (c:CandidateContribution {{contribution_key: $contribution_key}})
            SET c.candidate_id=$candidate_id, c.revision=$revision, c.decision_id=$decision_id,
                c.active=true, c.updated_at=datetime()
            MERGE (c)-[:SUPPORTS_FACT]->(f)
            MERGE (d:ReviewDecision {{decision_id: $decision_id}})
            SET d.candidate_id=$candidate_id, d.revision=$revision, d.audit_id=$audit_id,
                d.decision_version=$decision_version, d.updated_at=datetime()
            MERGE (d)-[:AUTHORIZED]->(c)
        """, **dict(values))
        evidence_items = item.get("evidence_items") or item.get("evidence_bundle", {}).get("items", [])
        for evidence in evidence_items:
            if not isinstance(evidence, Mapping):
                continue
            evidence = dict(evidence)
            evidence_key = Neo4jImporter.make_evidence_key(values["fact_key"], evidence)
            stance = evidence.get("semantic_status", "unknown")
            relationship = {
                "supported": "SUPPORTED_BY", "contradicted": "CONTRADICTED_BY"
            }.get(stance, "HAS_CONTEXT")
            section_id = evidence.get("section_id") or ""
            tx.run(f"""
                MATCH (f:Fact {{fact_key: $fact_key}})
                MERGE (e:Evidence {{evidence_key: $evidence_key}})
                SET e.document_id=$document_id, e.document_version=$document_version,
                    e.chunk_id=$chunk_id, e.section_id=$section_id, e.sentence_id=$sentence_id,
                    e.quote=$quote, e.start=$start, e.end=$end, e.offset_basis=$offset_basis,
                    e.semantic_status=$semantic_status, e.updated_at=datetime()
                MERGE (f)-[:{relationship}]->(e)
                WITH e WHERE $chunk_id <> ''
                MERGE (c:Chunk {{chunk_key: $chunk_key}})
                SET c.chunk_id=$chunk_id, c.document_version=$document_version,
                    c.updated_at=datetime()
                MERGE (e)-[:FROM_CHUNK]->(c)
                WITH c WHERE $section_id <> ''
                MERGE (s:Section {{section_key: $section_key}})
                SET s.section_id=$section_id, s.document_version=$document_version,
                    s.updated_at=datetime()
                MERGE (c)-[:BELONGS_TO]->(s)
                WITH s WHERE $document_id <> ''
                MERGE (doc:Document {{document_key: $document_key}})
                SET doc.document_id=$document_id, doc.document_version=$document_version,
                    doc.updated_at=datetime()
                MERGE (doc)-[:HAS_SECTION]->(s)
            """, fact_key=values["fact_key"], evidence_key=evidence_key,
                   document_id=evidence.get("document_id") or item.get("document_id", ""),
                   document_version=evidence.get("document_version") or item.get("document_version", ""),
                   document_key=hashlib.sha256(
                       (str(evidence.get("document_id") or item.get("document_id", "")) + "\n" +
                        str(evidence.get("document_version") or item.get("document_version", ""))).encode("utf-8")
                   ).hexdigest(),
                   chunk_id=evidence.get("chunk_id", ""),
                   chunk_key=hashlib.sha256(
                       (str(evidence.get("document_version") or item.get("document_version", "")) + "\n" +
                        str(evidence.get("chunk_id", ""))).encode("utf-8")
                   ).hexdigest(),
                   section_id=section_id,
                   section_key=hashlib.sha256(
                       (str(evidence.get("document_version") or item.get("document_version", "")) + "\n" +
                        str(section_id)).encode("utf-8")
                   ).hexdigest(),
                   sentence_id=evidence.get("sentence_id"), quote=evidence.get("quote") or evidence.get("text", ""),
                   start=evidence.get("start"), end=evidence.get("end"),
                   offset_basis=evidence.get("offset_basis", "chunk"), semantic_status=stance)

    def import_triplet(self, session: Any, item: Mapping[str, Any]) -> bool:
        if not self.is_importable(item):
            return False
        values = self._prepare(item)
        session.execute_write(self._import_transaction, values, item)
        return True

    @staticmethod
    def _retract_transaction(tx: Any, candidate_id: str, revision: int, decision_version: int) -> None:
        tx.run("""
            MATCH (c:CandidateContribution {candidate_id: $candidate_id, revision: $revision})-[:SUPPORTS_FACT]->(f:Fact)
            SET c.active=false, c.retracted_at=datetime(), c.applied_decision_version=$decision_version
            WITH f
            OPTIONAL MATCH (other:CandidateContribution {active: true})-[:SUPPORTS_FACT]->(f)
            WITH f, count(other) AS remaining
            SET f.active = remaining > 0, f.updated_at=datetime(),
                f.applied_decision_version=$decision_version
            WITH f, remaining
            OPTIONAL MATCH (:Entity)-[edge {fact_key: f.fact_key}]->(:Entity)
            SET edge.active = remaining > 0, edge.updated_at=datetime()
        """, candidate_id=candidate_id, revision=revision, decision_version=decision_version)

    def retract(self, session: Any, *, candidate_id: str, revision: int, decision_version: int) -> None:
        session.execute_write(self._retract_transaction, candidate_id, revision, decision_version)


def authoritative_item(repository: ReviewRepository, outbox: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    candidate = repository.get_candidate(outbox["candidate_id"])
    decision = repository.latest_decision(outbox["candidate_id"])
    if decision is None:
        return None
    if decision.get("decision_id") != outbox["decision_id"]:
        return None
    if int(decision.get("decision_version", -1)) != int(outbox["decision_version"]):
        return None
    evidence_items = [dict(item) for item in candidate.get("evidence_items") or [] if isinstance(item, Mapping)]
    audit_id = decision.get("audit_id")
    if decision.get("decision") in {"auto_accept", "auto_reject"}:
        from review_agent.proposal_resolver import selected_proposal
        proposal, votes = selected_proposal(repository, candidate, decision)
        evidence_items = [dict(item) for item in proposal["evidence_bundle"]["items"]]
        supporting = {eid for vote in votes if vote.get("response_status") == "ok" for eid in vote.get("supporting_evidence_ids", [])}
        contradicting = {eid for vote in votes if vote.get("response_status") == "ok" for eid in vote.get("counter_evidence_ids", [])}
        for evidence in evidence_items:
            eid = evidence["evidence_id"]
            evidence["semantic_status"] = ("supported" if eid in supporting and eid not in contradicting else
                                           "contradicted" if eid in contradicting and eid not in supporting else "unknown")
    elif audit_id:
        try:
            audit = repository.get_audit(outbox["candidate_id"], str(audit_id))
            rounds = audit.get("rounds") or []
            if rounds:
                evidence_items = [
                    dict(item) for item in (rounds[-1].get("evidence_bundle") or {}).get("items", [])
                    if isinstance(item, Mapping)
                ]
                supporting = {
                    evidence_id for vote in audit.get("votes") or []
                    if vote.get("response_status") == "ok"
                    for evidence_id in vote.get("supporting_evidence_ids") or []
                }
                contradicting = {
                    evidence_id for vote in audit.get("votes") or []
                    if vote.get("response_status") == "ok"
                    for evidence_id in vote.get("counter_evidence_ids") or []
                }
                for evidence in evidence_items:
                    evidence_id = evidence.get("evidence_id")
                    if evidence_id in supporting and evidence_id not in contradicting:
                        evidence["semantic_status"] = "supported"
                    elif evidence_id in contradicting and evidence_id not in supporting:
                        evidence["semantic_status"] = "contradicted"
                    else:
                        evidence["semantic_status"] = "unknown"
        except NotFoundError:
            # Import eligibility remains decision-based; missing audit evidence is visible as an empty bundle.
            evidence_items = []
    selected_human_evidence = set(decision.get("evidence_ids") or [])
    if decision.get("source") == "human_confirmed" and selected_human_evidence:
        for evidence in evidence_items:
            if evidence.get("evidence_id") in selected_human_evidence:
                evidence["semantic_status"] = "supported"
    return {
        **candidate, "evidence_items": evidence_items,
        "latest_decision": {**decision, "active": True},
    }


def dry_run(repository: ReviewRepository, *, run_id: Optional[str] = None) -> Dict[str, Any]:
    events = repository.list_outbox(status="pending", run_id=run_id, limit=1_000_000)
    counts = {"upsert": 0, "retract": 0, "stale": 0}
    reasons: Dict[str, int] = {}
    for event in events:
        if authoritative_item(repository, event) is None:
            counts["stale"] += 1
            reasons["STALE_DECISION"] = reasons.get("STALE_DECISION", 0) + 1
        else:
            counts[event["operation"]] = counts.get(event["operation"], 0) + 1
    return {"run_id": run_id, "pending": len(events), "counts": counts, "reasons": reasons}


def apply_outbox(
    repository: ReviewRepository, importer: Neo4jImporter, *, run_id: Optional[str] = None,
) -> Dict[str, int]:
    result = {"completed": 0, "failed": 0, "stale": 0}
    events = [
        *repository.list_outbox(status="pending", run_id=run_id, limit=1_000_000),
        *repository.list_outbox(status="failed", run_id=run_id, limit=1_000_000),
    ]
    for event in events:
        item = authoritative_item(repository, event)
        if item is None:
            repository.mark_outbox(event["event_id"], "retracted", "stale decision")
            result["stale"] += 1
            continue
        try:
            with importer.driver.session() as session:
                if event["operation"] == "retract":
                    importer.retract(session, candidate_id=event["candidate_id"], revision=event["revision"],
                                     decision_version=event["decision_version"])
                elif not importer.import_triplet(session, item):
                    raise ValueError("current decision is not importable")
            if authoritative_item(repository, event) is None:
                if event["operation"] == "upsert":
                    with importer.driver.session() as session:
                        importer.retract(
                            session, candidate_id=event["candidate_id"], revision=event["revision"],
                            decision_version=event["decision_version"],
                        )
                repository.mark_outbox(event["event_id"], "retracted", "decision changed during import")
                result["stale"] += 1
                continue
            repository.mark_outbox(event["event_id"], "completed")
            result["completed"] += 1
        except Exception as exc:
            repository.mark_outbox(event["event_id"], "failed", str(exc))
            result["failed"] += 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Project authoritative v3 review decisions to Neo4j.")
    parser.add_argument("--run-id")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--database", default=str(ROOT / "runs" / "v3" / "review.sqlite3"))
    parser.add_argument("--ontology")
    args = parser.parse_args()
    repository = ReviewRepository(args.database)
    repository.initialize()
    if args.dry_run:
        print(json.dumps(dry_run(repository, run_id=args.run_id), ensure_ascii=False, indent=2))
        return
    if not args.run_id:
        raise SystemExit("--run-id is required with --apply so one frozen ontology is used")
    manifest = repository.get_run(args.run_id)
    ontology_path = Path(args.ontology or manifest.get("ontology_path") or ROOT / "ontology" / "ontology.json")
    if not ontology_path.is_absolute():
        ontology_path = ROOT / ontology_path
    snapshot = load_snapshot(ontology_path, manifest.get("ontology_hash"))
    importer = Neo4jImporter(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        os.environ.get("NEO4J_USER", "neo4j"), os.environ.get("NEO4J_PASSWORD", ""),
        relation_types=relation_type_map(snapshot.source_path or ontology_path),
    )
    try:
        result = apply_outbox(repository, importer, run_id=args.run_id)
    finally:
        importer.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
