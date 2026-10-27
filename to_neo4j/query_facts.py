"""GraphRAG reads full admitted facts, including qualifiers and provenance."""
import json


def query_facts(driver, *, entity_id=None, relation_id=None, database="neo4j", limit=50):
    limit = max(1, min(int(limit), 500))
    with driver.session(database=database) as session:
        rows = list(session.run("""MATCH (f:Fact {active:true})
            WHERE ($entity IS NULL OR f.head_key=$entity OR f.tail_key=$entity)
              AND ($relation IS NULL OR f.relation_id=$relation)
              AND EXISTS { MATCH (:CandidateContribution {active:true})-[:CONTRIBUTES_TO]->(f) }
            WITH f ORDER BY f.fact_key LIMIT $limit
            MATCH (c:CandidateContribution {active:true})-[:CONTRIBUTES_TO]->(f)
            OPTIONAL MATCH (c)-[:HAS_ASSESSMENT]->(a:EvidenceAssessment {active:true})-[:ASSESSES]->(e:Evidence)
            RETURN properties(f) AS fact, collect(DISTINCT properties(c)) AS contributions,
                   collect(DISTINCT {assessment:a.assessment_json,anchor:e.anchor_json,decision_id:a.decision_id}) AS assessments""",
            entity=entity_id, relation=relation_id, limit=limit))
    result = []
    for row in rows:
        fact = dict(row["fact"])
        for key in ("qualifiers", "qualifier_status", "literal"):
            fact[key] = json.loads(fact.pop(key + "_json")) if fact.get(key + "_json") else None
        result.append({"fact": fact, "contributions": row["contributions"], "assessments": [
            {"assessment": json.loads(a["assessment"]), "anchor": json.loads(a["anchor"]), "decision_id": a["decision_id"]}
            for a in row["assessments"] if a["assessment"]]})
    return result
