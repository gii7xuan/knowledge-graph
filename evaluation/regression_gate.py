"""Engineering readiness is independent of whether semantic votes agree."""
from collections import Counter
import json


TERMINAL_REQUEST_STATES = {'completed', 'failed', 'not_sent'}


def request_accounting(connection):
    states = dict(Counter(row[0] for row in connection.execute('SELECT state FROM request_attempts')))
    reservations = dict(Counter(row[0] for row in connection.execute('SELECT status FROM validation_requests')))
    unresolved = sum(count for state, count in states.items() if state not in TERMINAL_REQUEST_STATES)
    unresolved_reservations = sum(count for state, count in reservations.items() if state not in {'completed', 'failed'})
    planner=[]
    columns={r[1] for r in connection.execute('PRAGMA table_info(request_attempts)')}
    durable=({r[0]:(r[1],json.loads(r[2] or '{}')) for r in connection.execute('SELECT attempt_id,state,result_json FROM request_attempts')}
             if {'attempt_id','result_json'}<=columns else {})
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='planner_steps'").fetchone():
        for row in connection.execute('SELECT record_json FROM planner_steps'):
            for attempt in json.loads(row[0]).get('attempts') or []:
                saved=durable.get(attempt.get('attempt_id'))
                if saved:
                    state,result=saved
                    attempt={**result,'outcome_unknown':state not in TERMINAL_REQUEST_STATES,
                             'response_status':result.get('response_status') or 'started'}
                planner.append(attempt)
    planner_states=dict(Counter(attempt.get('response_status') for attempt in planner))
    unresolved_planner=sum(bool(attempt.get('outcome_unknown')) or attempt.get('response_status') not in
        {'ok','invalid_action','invalid_json','invalid_contract','invalid_reference','api_error','not_sent'} for attempt in planner)
    return {'request_states': states, 'reservation_states': reservations,
            'unresolved_attempts': unresolved, 'unresolved_reservations': unresolved_reservations,
            'planner_request_states':planner_states,'unresolved_planner_requests':unresolved_planner,
            'requests_settled': not (unresolved or unresolved_reservations or unresolved_planner)}


def engineering_gate(cases, accounting, *, outbox=0, applied=0):
    terminal = bool(cases) and all(
        row['outcome']['execution_status'] not in {'running', 'not_started'} and
        row['outcome'].get('execution_reason') for row in cases)
    failures = any(row.get('trace_defect_recurred') or row.get('systematic_version_or_binding_error') or
        (row.get('runtime_error') or {}).get('reason_code') == 'RUNTIME_EXCEPTION' for row in cases)
    return {'all_have_explainable_terminal_state': terminal,
            'engineering_gate_passed': bool(terminal and not failures and accounting['requests_settled'] and not outbox and not applied),
            **accounting}
