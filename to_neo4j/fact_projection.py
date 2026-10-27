"""Fenced desired-state projection. Every mutation for one candidate is atomic."""
import hashlib
from review_agent.contracts import canonical_json
from schema.fact_identity import literal_identity


def direct_edge_eligible(triple):
    q = triple["qualifiers"]
    return (triple.get("tail_kind", "entity") == "entity" and q["polarity"] == "positive"
            and q["modality"] in (None, "asserted")
            and all(q[k] is None for k in ("condition", "time_scope", "applicability", "quantifier")))


def anchor_payload(evidence):
    # A source anchor's identity excludes retrieval route and reviewer stance.
    return {k: evidence.get(k) for k in ("document_id", "document_version", "source_snapshot_id", "source_index_hash",
        "offset_basis", "source_anchor_kind", "start", "end", "chunk_id", "section_id", "span_chunk_ids", "quote")}


def anchor_key(evidence):
    from evidence.verification import span_key
    return hashlib.sha256(canonical_json(span_key(evidence)).encode()).hexdigest()


def prepare(contribution):
    candidate, decision = contribution["candidate"], contribution["decision"]
    t = candidate["triple"]
    literal = literal_identity(t) if t.get("tail_kind") == "literal" else None
    return {"fact_key": candidate["fact_key"], "candidate_id": candidate["candidate_id"], "revision": candidate["revision"],
            "contribution_key": f"{candidate['candidate_id']}:{candidate['revision']}:{decision['decision_id']}",
            "decision_id": decision["decision_id"], "decision_version": decision["decision_version"],
            "head_key": t["head_entity_id"], "head": t["head"], "head_type": t.get("head_type"),
            "tail_key": t.get("tail_entity_id") if literal is None else None, "tail": t.get("tail"), "tail_type": t.get("tail_type"),
            "tail_kind": t.get("tail_kind", "entity"), "relation_id": t["relation_id"], "relation_semantic_hash": t["relation_semantic_hash"],
            "qualifiers_json": canonical_json(t["qualifiers"]), "qualifier_status_json": canonical_json(t["qualifier_status"]),
            "literal_json": canonical_json(literal) if literal else None,
            "literal_key": hashlib.sha256(canonical_json(literal).encode()).hexdigest() if literal else None,
            "direct_edge": direct_edge_eligible(t), "release_id": candidate["ontology_release_id"],
            "ontology_version": candidate["ontology_version"], "profile_hash": candidate["profile_hash"],
            "document_id": candidate.get("document_id"), "document_version": candidate.get("document_version"),
            "bundle_hash": contribution["bundle_hash"]}


class Neo4jImporter:
    def __init__(self, uri, user="neo4j", password="", *, database="neo4j", repository=None, create_constraints=True, relation_types=None):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(user, password) if user else None)
        self.database, self.repository = database, repository
        if create_constraints:
            with self.driver.session(database=database) as session:
                for label, key in (("Entity", "entity_key"), ("Fact", "fact_key"), ("Evidence", "evidence_key"),
                                   ("CandidateProjection", "candidate_id"), ("FactProjectionLock", "fact_key"),
                                   ("CandidateContribution", "contribution_key"), ("EvidenceAssessment", "assessment_key"),
                                   ("ReviewDecision", "decision_id"), ("LiteralValue", "literal_key")):
                    session.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE").consume()

    def close(self): self.driver.close()

    def is_importable(self, item):
        if not getattr(self, "repository", None): return False
        state = self.repository.current_admitted_contributions(item["candidate_id"])
        return bool(state["desired"] and state["revision"] == item.get("revision"))

    def import_triplet(self, session, item):
        if not self.is_importable(item): return False
        self.reconcile(self.repository.current_admitted_contributions(item["candidate_id"]))
        return True

    def reconcile(self, state):
        with self.driver.session(database=self.database) as session:
            return session.execute_write(self._transaction, state)

    @staticmethod
    def _transaction(tx, state):
        cid, version, state_hash = state["candidate_id"], state["projection_version"], state["state_hash"]
        fence = tx.run("""MERGE (p:CandidateProjection {candidate_id:$cid})
            SET p._lock=coalesce(p._lock,0)+1
            RETURN coalesce(p.projection_version,0) AS version,p.state_hash AS hash""", cid=cid).single()
        if fence["version"] > version: return {"status": "stale", "version": fence["version"]}
        if fence["version"] == version:
            if fence["hash"] != state_hash: raise ValueError("same graph projection version has a different state hash")
        desired = [(prepare(c), c) for c in state["desired"]]
        old = list(tx.run("""MATCH (c:CandidateContribution {candidate_id:$cid})-[:CONTRIBUTES_TO|SUPPORTS_FACT]->(f:Fact)
            OPTIONAL MATCH (f)-[:HEAD]->(h:Entity) OPTIONAL MATCH (f)-[:TAIL]->(t:Entity)
            OPTIONAL MATCH (lh:Entity)-[:FACT_SUBJECT]->(f) OPTIONAL MATCH (f)-[:FACT_OBJECT]->(lt:Entity)
            RETURN DISTINCT f.fact_key AS key, coalesce(f.head_key,h.entity_key,lh.entity_key) AS head, coalesce(f.tail_key,t.entity_key,lt.entity_key) AS tail""", cid=cid))
        fact_keys = sorted({r["key"] for r in old} | {v["fact_key"] for v, _ in desired})
        for key in fact_keys:
            tx.run("MERGE (l:FactProjectionLock {fact_key:$key}) SET l._lock=coalesce(l._lock,0)+1", key=key).consume()
        entity_keys = {k for r in old for k in (r["head"], r["tail"]) if k}
        entity_keys.update(k for v, _ in desired for k in (v["head_key"], v["tail_key"]) if k)
        for key in sorted(entity_keys):
            tx.run("MERGE (e:Entity {entity_key:$key}) SET e._lock=coalesce(e._lock,0)+1", key=key).consume()
        for key in sorted({v["literal_key"] for v, _ in desired if v["literal_key"]}):
            tx.run("MERGE (l:LiteralValue {literal_key:$key}) SET l._lock=coalesce(l._lock,0)+1",key=key).consume()
        for key in sorted({anchor_key(e) for _,c in desired for e in c["evidence_bundle"]["items"]}):
            tx.run("MERGE (e:Evidence {evidence_key:$key}) SET e._lock=coalesce(e._lock,0)+1",key=key).consume()
        tx.run("MATCH (c:CandidateContribution {candidate_id:$cid}) SET c.active=false", cid=cid).consume()
        tx.run("MATCH (a:EvidenceAssessment {candidate_id:$cid}) SET a.active=false", cid=cid).consume()
        for values, contribution in desired:
            tx.run("""MERGE (f:Fact {fact_key:$v.fact_key}) SET f += $properties
                MERGE (h:Entity {entity_key:$v.head_key}) SET h.display_name=$v.head MERGE (f)-[:HEAD]->(h)
                MERGE (c:CandidateContribution {contribution_key:$v.contribution_key}) SET c += $v,c.active=true
                MERGE (c)-[:CONTRIBUTES_TO]->(f)
                MERGE (d:ReviewDecision {decision_id:$v.decision_id}) SET d.decision_json=$decision
                MERGE (d)-[:AUTHORIZES]->(c)""", v=values,
                properties={k: values[k] for k in ("head_key", "tail_key", "tail_kind", "relation_id", "relation_semantic_hash", "qualifiers_json", "qualifier_status_json", "literal_json", "direct_edge")},
                decision=canonical_json(contribution["decision"])).consume()
            tx.run("MATCH (c:CandidateContribution {contribution_key:$ck})-[r:CITES]->(:Evidence) DELETE r",ck=values["contribution_key"]).consume()
            if values["tail_kind"] == "entity":
                tx.run("""MATCH (f:Fact {fact_key:$v.fact_key}) MERGE (t:Entity {entity_key:$v.tail_key})
                    SET t.display_name=$v.tail MERGE (f)-[:TAIL]->(t)""", v=values).consume()
            else:
                tx.run("""MATCH (f:Fact {fact_key:$v.fact_key}) MERGE (l:LiteralValue {literal_key:$v.literal_key})
                    SET l.value_json=$v.literal_json MERGE (f)-[:VALUE]->(l)""", v=values).consume()
            for evidence in contribution["evidence_bundle"]["items"]:
                anchor, key = anchor_payload(evidence), anchor_key(evidence)
                tx.run("""MERGE (e:Evidence {evidence_key:$key}) SET e.anchor_json=$anchor REMOVE e.semantic_status
                    WITH e MATCH (c:CandidateContribution {contribution_key:$ck}) MERGE (c)-[:CITES]->(e)""",
                    key=key, anchor=canonical_json(anchor), ck=values["contribution_key"]).consume()
                for assessment in contribution["assessments"]:
                    if assessment["evidence_id"] != evidence["evidence_id"]: continue
                    assessment_key = hashlib.sha256(canonical_json([values["decision_id"], cid, assessment]).encode()).hexdigest()
                    tx.run("""MERGE (a:EvidenceAssessment {assessment_key:$key})
                        SET a.candidate_id=$cid,a.decision_id=$did,a.active=true,a.assessment_json=$assessment
                        WITH a MATCH (c:CandidateContribution {contribution_key:$ck}),(e:Evidence {evidence_key:$ek})
                        MERGE (c)-[:HAS_ASSESSMENT]->(a) MERGE (a)-[:ASSESSES]->(e)""",
                        key=assessment_key, cid=cid, did=values["decision_id"], assessment=canonical_json(assessment), ck=values["contribution_key"], ek=key).consume()
        for key in fact_keys:
            tx.run("""MATCH (f:Fact {fact_key:$key})
                OPTIONAL MATCH (c:CandidateContribution {active:true})-[:CONTRIBUTES_TO|SUPPORTS_FACT]->(f)
                WITH f,collect(DISTINCT c) AS cs SET f.active=size(cs)>0,f.contributor_count=size(cs),
                    f.release_ids=reduce(xs=[],c IN cs | CASE WHEN c.release_id IN xs THEN xs ELSE xs+c.release_id END),
                    f.ontology_versions=reduce(xs=[],c IN cs | CASE WHEN c.ontology_version IN xs THEN xs ELSE xs+c.ontology_version END),
                    f.profile_hashes=reduce(xs=[],c IN cs | CASE WHEN c.profile_hash IN xs THEN xs ELSE xs+c.profile_hash END)
                WITH f OPTIONAL MATCH (c:CandidateContribution {active:true})-[:CONTRIBUTES_TO|SUPPORTS_FACT]->(f)
                OPTIONAL MATCH (c)-[:CITES]->(e:Evidence)
                WITH f,count(DISTINCT e) AS anchors SET f.source_anchor_count=anchors""", key=key).consume()
            tx.run("MATCH (:Entity)-[r]->(:Entity) WHERE r.fact_key=$key DELETE r", key=key).consume()
            tx.run("""MATCH (f:Fact {fact_key:$key,active:true,direct_edge:true})-[:HEAD]->(h:Entity),
                (f)-[:TAIL]->(t:Entity) MERGE (h)-[r:KG_FACT {fact_key:$key}]->(t)
                SET r.relation_id=f.relation_id""", key=key).consume()
        tx.run("MATCH (p:CandidateProjection {candidate_id:$cid}) SET p.projection_version=$version,p.state_hash=$hash",
               cid=cid, version=version, hash=state_hash).consume()
        return {"status": "unchanged" if fence["version"] == version else "applied", "version": version, "affected_facts": fact_keys}
