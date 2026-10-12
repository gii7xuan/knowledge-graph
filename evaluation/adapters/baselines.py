"""Implemented non-GraphJudge baselines; GraphJudge is intentionally not faked."""

from typing import Any, Dict, Iterable, Mapping


def single_judge(vote: Mapping[str, Any]) -> Dict[str, Any]:
    verdict = vote.get("verdict") if vote.get("response_status") == "ok" else "uncertain"
    return {"method": "single_judge", "verdict": verdict}


def simple_majority(votes: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    counts = {"valid": 0, "invalid": 0, "uncertain": 0}
    for vote in votes:
        if vote.get("response_status") == "ok" and vote.get("verdict") in counts:
            counts[vote["verdict"]] += 1
    winner = max(counts, key=counts.get) if len(set(counts.values())) > 1 else "uncertain"
    return {"method": "simple_majority", "verdict": winner, "counts": counts}
