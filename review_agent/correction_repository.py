"""Atomic repair allowances shared by inline retries and later recovery."""
import json
import time
from uuid import uuid4
from .contracts import canonical_json,sha256_json


def initialize(connection):
    connection.execute('CREATE TABLE IF NOT EXISTS review_response_corrections('
        'correction_id TEXT PRIMARY KEY,scope_key TEXT NOT NULL UNIQUE,candidate_id TEXT NOT NULL,revision INTEGER NOT NULL,'
        'request_key TEXT NOT NULL,audit_id TEXT NOT NULL,reserved_tokens INTEGER NOT NULL,policy_hash TEXT NOT NULL,'
        'plan_json TEXT NOT NULL,state TEXT NOT NULL,attempt_id TEXT,actual_tokens INTEGER NOT NULL,created_at REAL NOT NULL,finished_at REAL)')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_response_correction_candidate ON review_response_corrections(candidate_id,revision)')


def allowance(repository,request_key,candidate,policy):
    limits=policy['limits']
    if limits['max_corrections_per_seat']==0:return 'field corrections disabled'
    with repository.connect() as c:
        family=c.execute('SELECT semantic_key FROM validation_request_families WHERE request_key=?',(request_key,)).fetchone()
        exists=c.execute('SELECT 1 FROM review_response_corrections WHERE request_key=? OR scope_key=?',
            (request_key,family[0] if family else '')).fetchone()
        rows=c.execute('SELECT reserved_tokens,actual_tokens FROM review_response_corrections WHERE candidate_id=? AND revision=?',
            (candidate['candidate_id'],candidate['revision'])).fetchall()
    if exists:return 'one field correction already reserved or attempted for this frozen seat'
    if len(rows)>=limits['max_calls_per_candidate_version']:return 'candidate correction call budget exhausted'
    if sum(max(r[0],r[1]) for r in rows)>=limits['max_reserved_tokens_per_candidate_version']:return 'candidate correction token budget exhausted'
    return None


def reserve(repository,scope_key,request_key,proposal,plan,reserved_tokens):
    candidate=proposal['candidate'];policy=plan['policy'];limits=policy['limits'];cid=candidate['candidate_id'];rev=candidate['revision']
    with repository.transaction(immediate=True) as c:
        if c.execute('SELECT 1 FROM review_response_corrections WHERE scope_key=?',(scope_key,)).fetchone():
            return None,'one field correction already reserved or attempted'
        rows=c.execute('SELECT reserved_tokens,actual_tokens FROM review_response_corrections WHERE candidate_id=? AND revision=?',(cid,rev)).fetchall()
        if not limits['max_corrections_per_seat'] or len(rows)>=limits['max_calls_per_candidate_version']:return None,'candidate correction call budget exhausted'
        if sum(max(r[0],r[1]) for r in rows)+reserved_tokens>limits['max_reserved_tokens_per_candidate_version']:return None,'candidate correction token budget exhausted'
        pending=c.execute("SELECT COALESCE(SUM(reserved_tokens),0) FROM review_response_corrections WHERE candidate_id=? AND state IN ('reserved','running','unknown')",(cid,)).fetchone()[0]
        for row in c.execute('SELECT limits_json,usage_json FROM review_cases WHERE candidate_id=?',(cid,)):
            ceiling=json.loads(row[0]).get('tokens');used=json.loads(row[1]).get('tokens',0)
            if ceiling is not None and used+pending+reserved_tokens>ceiling:return None,'remaining case token budget cannot cover field correction'
        audit=c.execute('SELECT context_json FROM audit_runs WHERE audit_id=?',(proposal['audit_id'],)).fetchone()
        if audit:
            context=json.loads(audit[0]);ceiling=context.get('budget_config',{}).get('max_total_tokens')
            if ceiling:
                known=0
                for row in c.execute("SELECT result_json FROM request_attempts WHERE json_extract(metadata_json,'$.binding.audit_id')=?",(proposal['audit_id'],)):
                    result=json.loads(row[0] or '{}');known+=sum(max(0,result.get(k) or 0) for k in ('input_tokens','output_tokens'))
                for row in c.execute('SELECT record_json FROM planner_steps WHERE audit_id=?',(proposal['audit_id'],)):
                    step=json.loads(row[0]);known+=sum(sum(max(0,a.get(k) or 0) for k in ('input_tokens','output_tokens')) for a in step.get('attempts',[]) or [step])
                queued=c.execute("SELECT COALESCE(SUM(reserved_tokens),0) FROM review_response_corrections WHERE audit_id=? AND state IN ('reserved','running','unknown')",(proposal['audit_id'],)).fetchone()[0]
                if known+queued+reserved_tokens>ceiling:return None,'remaining audit token budget cannot cover field correction'
        ident='correction_'+uuid4().hex
        c.execute('INSERT INTO review_response_corrections VALUES(?,?,?,?,?,?,?,?,?,\'reserved\',NULL,0,?,NULL)',
            (ident,scope_key,cid,rev,request_key,proposal['audit_id'],reserved_tokens,sha256_json(policy),canonical_json(plan),time.time()))
    return ident,None


def started(connection,correction_id,attempt_id):
    changed=connection.execute("UPDATE review_response_corrections SET state='running',attempt_id=? WHERE correction_id=? AND state='reserved'",(attempt_id,correction_id)).rowcount
    if changed!=1:raise ValueError('field correction reservation already dispatched')


def finished(connection,correction_id,attempt_id,status,result):
    tokens=sum(max(0,result.get(k) or 0) for k in ('input_tokens','output_tokens'))
    connection.execute('UPDATE review_response_corrections SET state=?,actual_tokens=MAX(actual_tokens,?),finished_at=? WHERE correction_id=? AND attempt_id=?',
        (status,tokens,time.time(),correction_id,attempt_id))
