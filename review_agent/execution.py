"""Execution failures distinct from semantic review decisions."""


class AuditPaused(RuntimeError):
    def __init__(self, vote):
        self.vote = dict(vote)
        super().__init__(str(vote.get("error") or "model account unavailable"))


def api_error_fields(error):
    """Classify permanent HTTP errors without retrying the same credentials."""
    import re
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    if status is None:
        match = re.search(r"(?:Error code:|HTTP)\s*(\d{3})\b", str(error), re.IGNORECASE)
        status = int(match.group(1)) if match else None
    permanent = status in {400, 401, 402, 403, 404, 405, 413, 422}
    transport_unknown = status is None and any(part in type(error).__name__.lower() for part in (
        "timeout", "connection", "protocol", "readerror", "writeerror"))
    return {"http_status": status, "retryable": not (permanent or transport_unknown),
            "pause_job": status == 402,
            **({"outcome_unknown": True} if transport_unknown else {}),
            "error_code": "INSUFFICIENT_BALANCE" if status == 402 else
                          "UNKNOWN_REQUEST_OUTCOME" if transport_unknown else
                          "PERMANENT_API_ERROR" if permanent else "TRANSIENT_API_ERROR"}
