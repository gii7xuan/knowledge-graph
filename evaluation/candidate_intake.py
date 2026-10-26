"""Source-level intake accounting; never a claim of factual accuracy."""

from collections import Counter


def intake_summary(candidates, report):
    canonical = [c for c in candidates if c.get("fact_key_state") == "canonical"]
    attempts = [attempt for scope in report.get("requests", []) for attempt in scope.get("attempts", [])]
    return {"candidate_count": len(candidates), "canonical_identity_candidate_count": len(canonical),
            "distinct_candidate_fact_keys": len({c["fact_key"] for c in canonical}),
            "provisional_identity_count": len(candidates)-len(canonical),
            "unmapped_relation_count": sum(not c["triple"].get("relation_id") for c in candidates),
            "unknown_polarity_count": sum(c["triple"]["qualifiers"].get("polarity") == "unknown" for c in candidates),
            "verified_source_span_count": len({(c["document_id"], c["document_version"], a["start"], a["end"])
                                                for c in candidates for a in c.get("evidence_items", [])}),
            "scope_status_counts": dict(Counter(scope["status"] for scope in report.get("requests", []))),
            "model_request_count": len(attempts), "failed_request_count": sum(a["status"] == "failed" for a in attempts),
            "reported_input_tokens": sum((a.get("usage") or {}).get("prompt_tokens") or 0 for a in attempts),
            "reported_output_tokens": sum((a.get("usage") or {}).get("completion_tokens") or 0 for a in attempts),
            "requests_with_unknown_token_usage": sum((a.get("usage") or {}).get("total_tokens") is None for a in attempts),
            "accuracy_evaluated": False}
