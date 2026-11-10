"""Isolated provider request process. Input is an anonymous pipe, output is fenced SQLite."""
import json
import sys
import time
from .repository import ReviewRepository
from .llm_clients import OpenAICompatibleClient


def main():
    descriptor=json.loads(sys.stdin.buffer.read().decode('utf-8'))
    repository=ReviewRepository(descriptor['database'])
    attempt=repository.request_attempt(descriptor['attempt_id'])
    voter={**descriptor['voter'],'client':OpenAICompatibleClient(**descriptor['client']),
           'max_output_tokens':descriptor['max_output_tokens']}
    proposal,feedback=attempt['metadata']['proposal'],attempt['metadata'].get('feedback')
    if attempt['state'] not in {'running','unknown'}: return
    provider_started_at=time.time()
    try:
        if repository.service_pause(attempt['metadata']['service_key']):
            vote={**attempt['metadata']['binding'],'response_status':'api_error','error_code':'SERVICE_PAUSED',
                  'error':'model service paused before dispatch','pause_job':True,'retryable':False,
                  'not_sent':True,'actual_calls':0}
        elif descriptor['task_type']=='triple_review':
            from .validators import _judge_validator_once
            vote=_judge_validator_once(voter,proposal,feedback)
        elif descriptor['task_type']=='ontology_review':
            from ontology_review.validators import judge_once
            vote=judge_once(voter,proposal,feedback)
        elif descriptor['task_type']=='ontology_competency_review':
            from ontology_review.semantic_competency import judge_once
            vote=judge_once(voter,proposal,feedback)
        elif descriptor['task_type']=='ontology_vote_clarification':
            from ontology_review.clarification import judge_once
            vote=judge_once(voter,proposal,feedback)
        elif descriptor['task_type']=='candidate_revision_review':
            from .revision_service import judge_revision_once
            vote=judge_revision_once(voter,proposal,feedback)
        elif descriptor['task_type']=='candidate_dispute_review':
            from .dispute_review import judge_once
            vote=judge_once(voter,proposal,feedback)
        else:
            raise ValueError('unknown frozen request task_type')
    except Exception as exc:
        from .execution import api_error_fields
        vote={**attempt['metadata']['binding'],'response_status':'api_error','error':str(exc),**api_error_fields(exc)}
    vote.update(provider_started_at=provider_started_at,
                queue_time_ms=int((provider_started_at-attempt['started_at'])*1000))
    if vote.get('pause_job'):
        repository.pause_model_service(attempt['metadata']['service_key'],vote)
    repository.finish_request_attempt(descriptor['attempt_id'],descriptor['owner'],vote,
                                      late=time.time()>attempt['deadline_at'])


if __name__=='__main__': main()
