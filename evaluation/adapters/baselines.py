"""Implemented non-GraphJudge baselines; GraphJudge is intentionally not faked."""

from typing import Any, Dict, Iterable, Mapping


def single_judge(vote: Mapping[str, Any]) -> Dict[str, Any]:
    verdict = vote.get("verdict") if vote.get("response_status") == "ok" else "uncertain"
    return {"method": "single_judge", "verdict": verdict}


def simple_majority(votes: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    counts = {"valid": 0, "invalid": 0, "uncertain": 0}
    population, errors = 0, 0
    for vote in votes:
        population += 1
        if vote.get("response_status") == "ok" and vote.get("verdict") in counts:
            counts[vote["verdict"]] += 1
        else: errors += 1
    highest = max(counts.values())
    winners = [v for v, n in counts.items() if n == highest]
    winner = winners[0] if highest > 0 and len(winners) == 1 and winners[0] != "uncertain" else "unresolved"
    return {"method": "simple_majority", "verdict": winner, "counts": counts,
            "population_count": population, "error_votes": errors}
