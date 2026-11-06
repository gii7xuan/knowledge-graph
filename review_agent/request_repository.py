"""Append-only attempt history and fenced late completion of frozen requests."""
import json
import time
from uuid import uuid4
from .contracts import canonical_json


class RequestRepository:
    def initialize_requests(self, connection):
        from .correction_repository import initialize
        initialize(connection)
        columns={r[1] for r in connection.execute('PRAGMA table_info(jobs)')}
        if 'lease_token' not in columns: connection.execute('ALTER TABLE jobs ADD COLUMN lease_token TEXT')
        for sql in (
            'CREATE TABLE IF NOT EXISTS request_attempts(attempt_id TEXT PRIMARY KEY,request_key TEXT NOT NULL,owner_token TEXT NOT NULL,state TEXT NOT NULL,started_at REAL NOT NULL,deadline_at REAL NOT NULL,response_received_at REAL,response_status TEXT,provider_request_id TEXT,raw_response_ref TEXT,recovery_of TEXT,recovery_reason TEXT,usage_state TEXT NOT NULL,metadata_json TEXT NOT NULL,result_json TEXT)',
            'CREATE INDEX IF NOT EXISTS idx_request_attempt_key ON request_attempts(request_key,started_at)',
            'CREATE TABLE IF NOT EXISTS request_attempt_events(event_id TEXT PRIMARY KEY,attempt_id TEXT NOT NULL,event TEXT NOT NULL,at REAL NOT NULL,detail_json TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS service_pauses(service_key TEXT PRIMARY KEY,status TEXT NOT NULL,reason_json TEXT NOT NULL,updated_at REAL NOT NULL)',
            'CREATE TABLE IF NOT EXISTS recovery_authorizations(grant_id TEXT PRIMARY KEY,audit_id TEXT NOT NULL,reason TEXT NOT NULL,extra_calls INTEGER NOT NULL,extra_tokens INTEGER NOT NULL,extra_seconds REAL NOT NULL,created_at REAL NOT NULL,result_json TEXT)',
            'CREATE TABLE IF NOT EXISTS job_resume_events(event_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,reason TEXT NOT NULL,at REAL NOT NULL)',
            'CREATE TABLE IF NOT EXISTS validation_request_families(request_key TEXT PRIMARY KEY,semantic_key TEXT NOT NULL)',
            'CREATE INDEX IF NOT EXISTS idx_request_family ON validation_request_families(semantic_key)',
            'CREATE TABLE IF NOT EXISTS case_budget_grants(grant_id TEXT PRIMARY KEY,authorization_key TEXT NOT NULL UNIQUE,case_id TEXT NOT NULL,actor TEXT NOT NULL,reason TEXT NOT NULL,additions_json TEXT NOT NULL,prior_limits_json TEXT NOT NULL,new_limits_json TEXT NOT NULL,usage_at_grant_json TEXT NOT NULL,created_at REAL NOT NULL)',
        ):
            connection.execute(sql)
        for row in connection.execute('SELECT * FROM validation_requests WHERE vote_json IS NOT NULL AND NOT EXISTS (SELECT 1 FROM request_attempts a WHERE a.request_key=validation_requests.request_key)').fetchall():
            from .contracts import sha256_json
            vote=json.loads(row['vote_json'])
            attempt_id='legacy_'+sha256_json([row['request_key'],row['owner'],row['updated_at']])[:32]
            try:
                from datetime import datetime
                started=datetime.fromisoformat(row['updated_at'].replace('Z','+00:00')).timestamp()
            except (ValueError,AttributeError):
                started=time.time()
            metadata={'legacy_import':True,'task_type':vote.get('task_type'),
                      'binding':{k:vote[k] for k in ('task_type','task_id','object_revision','audit_id','round_id','proposal_digest','voter_id','model_id','model_family','evidence_hash','response_contract_version') if k in vote}}
            connection.execute('INSERT OR IGNORE INTO request_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (attempt_id,row['request_key'],row['owner'],row['status'],started,started,started,
                 vote.get('response_status'),vote.get('response_id'),attempt_id+'#result_json',None,'legacy_history_import',
                 'known' if any(type(vote.get(k)) is int for k in ('input_tokens','output_tokens')) else 'unknown',
                 canonical_json(metadata),row['vote_json']))
        # Historical completed/timeout rows retain enough binding to reconstruct
        # triple input identity without credentials. Unbound legacy running rows
        # remain quarantined through their original frozen audit recovery path.
        for row in connection.execute('SELECT r.request_key,r.vote_json FROM validation_requests r '
            'WHERE r.vote_json IS NOT NULL AND NOT EXISTS '
            '(SELECT 1 FROM validation_request_families f WHERE f.request_key=r.request_key)').fetchall():
            vote=json.loads(row['vote_json'])
            if vote.get('task_type')!='triple_review': continue
            proposal=connection.execute('SELECT proposal_json FROM proposals WHERE audit_id=? AND round_id=?',
                                       (vote.get('audit_id'),vote.get('round_id'))).fetchone()
            if not proposal: continue
            from .request_identity import semantic_request_key
            try:
                semantic=semantic_request_key('triple_review',{'id':vote.get('voter_id'),
                    **{k:vote.get(k) for k in ('model_id','model_family','model_identity')}},json.loads(proposal[0]))
            except (ValueError,KeyError): continue
            connection.execute('INSERT OR IGNORE INTO validation_request_families VALUES(?,?)',(row['request_key'],semantic))

    def begin_request_attempt(self, key, owner, timeout, metadata, recovery_reason=None):
        from .repository import ConflictError
        attempt_id = 'attempt_' + uuid4().hex
        now = time.time()
        with self.transaction(immediate=True) as c:
            row = c.execute('SELECT * FROM validation_requests WHERE request_key=?', (key,)).fetchone()
            if not row or row['owner'] != owner or row['status'] not in {'running','failed'}:
                raise ConflictError('request is no longer owned')
            c.execute("UPDATE validation_requests SET status='running' WHERE request_key=? AND owner=?",(key,owner))
            prior = c.execute('SELECT attempt_id FROM request_attempts WHERE request_key=? ORDER BY started_at DESC LIMIT 1', (key,)).fetchone()
            c.execute('INSERT INTO request_attempts VALUES(?,?,?,\'running\',?,?,NULL,NULL,NULL,NULL,?,?,\'unknown\',?,NULL)',
                      (attempt_id,key,owner,now,now+timeout,prior[0] if prior else None,
                       recovery_reason or ('known_failure_retry' if prior else None),canonical_json(metadata)))
            c.execute('INSERT INTO request_attempt_events VALUES(?,?,?,?,?)',
                      (uuid4().hex,attempt_id,'started',now,'{}'))
            correction=(metadata.get('feedback') or {}).get('correction_id')
            if correction:
                from .correction_repository import started
                started(c,correction,attempt_id)
        return attempt_id

    def finish_request_attempt(self, attempt_id, owner, vote, *, late=False):
        from .repository import ConflictError
        now = time.time()
        status = 'not_sent' if vote.get('not_sent') else 'completed' if vote.get('response_status') == 'ok' else 'unknown' if vote.get('outcome_unknown') else 'failed'
        with self.transaction(immediate=True) as c:
            row = c.execute('SELECT * FROM request_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if not row or row['owner_token'] != owner:
                raise ConflictError('attempt owner changed')
            if row['state'] in {'completed','failed','not_sent'}:
                return False  # A waiting caller cannot replace a committed late result with timeout.
            metadata = json.loads(row['metadata_json'])
            correction=(metadata.get('feedback') or {}).get('correction_id')
            if correction:
                from .correction_repository import finished
                finished(c,correction,attempt_id,status,vote)
            bound = metadata.get('binding', {})
            if any(vote.get(k) != value for k,value in bound.items()):
                raise ConflictError('late result differs from frozen attempt binding')
            stored = {**vote,'attempt_id':attempt_id,'request_key':row['request_key'],'late_response':late}
            c.execute('UPDATE request_attempts SET state=?,response_received_at=?,response_status=?,provider_request_id=?,raw_response_ref=?,usage_state=?,result_json=? WHERE attempt_id=? AND owner_token=?',
                      (status,now,vote.get('response_status'),vote.get('response_id'),
                       attempt_id + '#result_json', 'known' if any(type(vote.get(k)) is int for k in ('input_tokens','output_tokens')) else 'unknown',
                       canonical_json(stored),attempt_id,owner))
            c.execute('INSERT INTO request_attempt_events VALUES(?,?,?,?,?)',
                      (uuid4().hex,attempt_id,'late_result' if late else status,now,canonical_json({'response_status':vote.get('response_status')})))
            total=sum(max(0,vote.get(k) or 0) for k in ('input_tokens','output_tokens'))
            if total:
                proposal=metadata.get('proposal',{})
                candidate_id=(proposal.get('candidate') or proposal.get('before') or {}).get('candidate_id')
                if candidate_id:
                    cases=[r[0] for r in c.execute('SELECT case_id FROM review_cases WHERE candidate_id=?',(candidate_id,))]
                else:
                    cases=[r[0] for r in c.execute('SELECT rc.case_id FROM review_cases rc JOIN ontology_extension_subscriptions s ON s.candidate_id=rc.candidate_id WHERE s.extension_id=?',
                                                   (proposal.get('task_id'),))]
                if cases: self._charge_cases(c,cases,{'tokens':total},'request_tokens:'+attempt_id,metadata['task_type'],binding_task_id(metadata))
            latest = c.execute('SELECT attempt_id FROM request_attempts WHERE request_key=? ORDER BY started_at DESC LIMIT 1', (row['request_key'],)).fetchone()
            if latest[0] == attempt_id:
                c.execute('UPDATE validation_requests SET status=?,vote_json=?,updated_at=? WHERE request_key=? AND owner=? AND status IN (\'running\',\'unknown\')',
                          ('failed' if status=='not_sent' else status,canonical_json(stored),str(now),row['request_key'],owner))
        return True

    def request_attempt(self, attempt_id):
        with self.connect() as c:
            row = c.execute('SELECT * FROM request_attempts WHERE attempt_id=?',(attempt_id,)).fetchone()
        if not row: return None
        return {**dict(row),'metadata':json.loads(row['metadata_json']), 'result':json.loads(row['result_json'] or '{}')}

    def request_history(self, key):
        with self.connect() as c:
            rows=c.execute('SELECT attempt_id FROM request_attempts WHERE request_key=? ORDER BY started_at',(key,)).fetchall()
        return [self.request_attempt(r[0]) for r in rows]

    def record_recovered_vote(self, audit_id, proposal, vote):
        from .repository import ConflictError
        with self.transaction(immediate=True) as c:
            old=c.execute('SELECT * FROM validator_votes WHERE audit_id=? AND round_id=? AND voter_id=?',
                          (audit_id,proposal['round_id'],vote['voter_id'])).fetchone()
            if old and old['response_status']=='ok':
                raise ConflictError('a valid response, including uncertain/invalid, is immutable')
            if vote.get('proposal_digest')!=proposal['proposal_digest']:
                raise ConflictError('recovered vote differs from frozen proposal')
            current=c.execute('SELECT current_revision FROM candidates WHERE candidate_id=?',
                              (proposal['candidate']['candidate_id'],)).fetchone()
            if current[0]!=proposal['candidate']['revision']:
                raise ConflictError('historical response cannot replace a newer revision vote')
            if old:
                c.execute('INSERT INTO validator_attempts VALUES(?,?,?,?,?,?,?,?)',
                          ('attempt_'+uuid4().hex,audit_id,proposal['round_id'],vote['voter_id'],
                           old['response_status'],0,old['vote_json'],str(time.time())))
                c.execute('UPDATE validator_votes SET response_status=?,verdict=?,vote_json=? '
                          'WHERE audit_id=? AND round_id=? AND voter_id=? AND response_status!=\'ok\'',
                          (vote['response_status'],vote.get('verdict'),canonical_json(vote),audit_id,proposal['round_id'],vote['voter_id']))
            else:
                c.execute('INSERT INTO validator_votes VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (audit_id,proposal['round_id'],vote['voter_id'],proposal['proposal_digest'],vote.get('model_id',''),
                           vote.get('model_family',''),vote['response_status'],vote.get('verdict'),canonical_json(vote),str(time.time())))

    def confirm_request_not_executed(self, key, *, attempt_id, reason, evidence_ref):
        """Operator/provider reconciliation only; never exposed to an LLM tool."""
        from .repository import ConflictError, ValidationError
        if not reason.strip() or not evidence_ref.strip(): raise ValidationError('provider reconciliation requires reason and evidence')
        with self.transaction(immediate=True) as c:
            row=c.execute('SELECT * FROM request_attempts WHERE attempt_id=? AND request_key=?',(attempt_id,key)).fetchone()
            if not row or row['state']!='unknown': raise ConflictError('only reconciled unknown requests can be released')
            c.execute("UPDATE request_attempts SET state='not_sent',recovery_reason=? WHERE attempt_id=?",(reason,attempt_id))
            c.execute('INSERT INTO request_attempt_events VALUES(?,?,?,?,?)',(uuid4().hex,attempt_id,'confirmed_not_executed',time.time(),canonical_json({'reason':reason,'evidence_ref':evidence_ref})))
            c.execute("UPDATE validation_requests SET status='failed' WHERE request_key=? AND owner=? AND status='unknown'",(key,row['owner_token']))

    def pause_model_service(self, key, reason):
        with self.transaction(immediate=True) as c:
            c.execute("INSERT INTO service_pauses VALUES(?,'paused',?,?) ON CONFLICT(service_key) DO UPDATE SET status='paused',reason_json=CASE WHEN service_pauses.status='paused' THEN service_pauses.reason_json ELSE excluded.reason_json END,updated_at=excluded.updated_at",
                      (key,canonical_json(reason),time.time()))
            c.execute("UPDATE jobs SET status='paused',progress_json=? WHERE status='queued' AND job_type IN ('ontology_extension','audit')",(canonical_json({'reason_code':'SERVICE_PAUSED','service_key':key}),))

    def service_pause(self, key):
        with self.connect() as c:
            row=c.execute("SELECT * FROM service_pauses WHERE service_key=? AND status='paused'",(key,)).fetchone()
        return dict(row) if row else None

    def resume_model_service(self, key, probe):
        from .repository import ValidationError
        if probe.get('service_key')!=key or probe.get('response_status')!='ok' or not probe.get('response_id'):
            raise ValidationError('successful same-service probe with response identity required')
        with self.transaction(immediate=True) as c:
            c.execute("UPDATE service_pauses SET status='available',reason_json=?,updated_at=? WHERE service_key=?",(canonical_json(probe),time.time(),key))

    def quarantine_gateway_timeouts(self,reason):
        from .repository import ValidationError
        if not str(reason).strip(): raise ValidationError('quarantine reason required')
        changed=[]
        with self.transaction(immediate=True) as c:
            rows=c.execute("SELECT * FROM request_attempts WHERE state='failed' AND json_extract(result_json,'$.http_status') IN (408,504)").fetchall()
            for row in rows:
                vote=json.loads(row['result_json'])
                vote['previous_classification']={k:vote.get(k) for k in ('error_code','retryable','outcome_unknown')}
                vote.update(error_code='GATEWAY_REQUEST_OUTCOME_UNKNOWN',retryable=False,outcome_unknown=True)
                c.execute("UPDATE request_attempts SET state='unknown',result_json=? WHERE attempt_id=?",(canonical_json(vote),row['attempt_id']))
                c.execute('INSERT INTO request_attempt_events VALUES(?,?,?,?,?)',(uuid4().hex,row['attempt_id'],
                    'gateway_timeout_reclassified_unknown',time.time(),canonical_json({'reason':reason})))
                latest=c.execute('SELECT vote_json FROM validation_requests WHERE request_key=?',(row['request_key'],)).fetchone()
                if latest and json.loads(latest[0] or '{}').get('attempt_id')==row['attempt_id']:
                    c.execute("UPDATE validation_requests SET status='unknown',vote_json=? WHERE request_key=?",(canonical_json(vote),row['request_key']))
                    for table in ('validator_votes','ontology_votes','revision_votes'):
                        records=c.execute('SELECT rowid,vote_json FROM '+table+" WHERE json_extract(vote_json,'$.request_key')=? AND json_extract(vote_json,'$.response_status')!='ok'",(row['request_key'],)).fetchall()
                        for record in records:
                            previous=json.loads(record['vote_json'])
                            previous.update(error_code='GATEWAY_REQUEST_OUTCOME_UNKNOWN',retryable=False,outcome_unknown=True,
                                gateway_quarantine_reason=reason)
                            c.execute('UPDATE '+table+' SET vote_json=? WHERE rowid=?',(canonical_json(previous),record['rowid']))
                changed.append(row['attempt_id'])
        return changed

    def authorize_case_budget(self,candidate_id,additions,*,reason,actor,authorization_key):
        from .repository import ValidationError,ConflictError
        from .case_budget import DEFAULT_CASE_LIMITS
        if (not str(reason).strip() or not str(actor).strip() or not str(authorization_key).strip()
                or not isinstance(additions,dict) or not additions or set(additions)-set(DEFAULT_CASE_LIMITS)
                or any(type(v) is not int or v<0 for v in additions.values()) or not any(additions.values())):
            raise ValidationError('positive additional budget and explicit operator authorization required')
        case_id=self.ensure_case(candidate_id)
        with self.transaction(immediate=True) as c:
            prior=c.execute('SELECT * FROM case_budget_grants WHERE authorization_key=?',(authorization_key,)).fetchone()
            if prior:
                if any(prior[k]!=v for k,v in {'case_id':case_id,'reason':reason,'actor':actor,'additions_json':canonical_json(additions)}.items()):
                    raise ConflictError('budget authorization identity cannot be reused for different terms')
                return dict(prior)
            account=c.execute('SELECT limits_json,usage_json FROM review_cases WHERE case_id=?',(case_id,)).fetchone()
            limits=json.loads(account['limits_json'])
            updated={**limits,**{k:limits.get(k,0)+v for k,v in additions.items()}}
            grant='case_grant_'+uuid4().hex
            c.execute('INSERT INTO case_budget_grants VALUES(?,?,?,?,?,?,?,?,?,?)',
                (grant,authorization_key,case_id,actor,reason,canonical_json(additions),canonical_json(limits),
                 canonical_json(updated),account['usage_json'],time.time()))
            c.execute('UPDATE review_cases SET limits_json=? WHERE case_id=?',(canonical_json(updated),case_id))
            return dict(c.execute('SELECT * FROM case_budget_grants WHERE grant_id=?',(grant,)).fetchone())

    def authorize_recovery_budget(self,audit_id,reason,extra_calls,extra_tokens,extra_seconds):
        from .repository import ValidationError
        if not str(reason).strip() or extra_calls<0 or extra_tokens<0 or extra_seconds<=0:
            raise ValidationError('recovery needs a reason, nonnegative extra budgets and positive time authorization')
        grant='grant_'+uuid4().hex
        with self.transaction(immediate=True) as c:
            audit=c.execute('SELECT candidate_id,status,context_json FROM audit_runs WHERE audit_id=?',(audit_id,)).fetchone()
            if not audit: raise ValidationError('audit not found')
            context=json.loads(audit['context_json'])
            if audit['status']=='running' and context.get('execution_deadline_epoch',0)>time.time():
                from .repository import ConflictError
                raise ConflictError('audit is owned by an active execution')
            case_id=self._ensure_case(c,audit['candidate_id'])
            limits=json.loads(c.execute('SELECT limits_json FROM review_cases WHERE case_id=?',(case_id,)).fetchone()[0])
            limits['llm_calls']+=extra_calls;limits['tokens']+=extra_tokens
            c.execute('UPDATE review_cases SET limits_json=? WHERE case_id=?',(canonical_json(limits),case_id))
            c.execute('INSERT INTO recovery_authorizations VALUES(?,?,?,?,?,?,?,NULL)',
                      (grant,audit_id,reason,extra_calls,extra_tokens,extra_seconds,time.time()))
            context.setdefault('recovery_deadline_history',[]).append({'previous_deadline':context.get('execution_deadline_epoch'),
                'previous_status':audit['status'],'grant_id':grant,'reason':reason})
            context.update(execution_deadline_epoch=time.time()+extra_seconds,recovery_grant_id=grant,
                           recovery_of_job_id=context.get('job_id'),job_id=None)
            c.execute("UPDATE audit_runs SET status='running',context_json=? WHERE audit_id=?",(canonical_json(context),audit_id))
        return grant

    def resume_job(self,job_id,reason):
        from .repository import ConflictError,ValidationError
        if not str(reason).strip(): raise ValidationError('job resume requires an explicit reason')
        with self.transaction(immediate=True) as c:
            if c.execute("SELECT 1 FROM service_pauses WHERE status='paused' LIMIT 1").fetchone():
                raise ConflictError('same-model service availability must be confirmed before queue resume')
            changed=c.execute("UPDATE jobs SET status='queued',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL WHERE job_id=? AND status='paused' AND cancel_requested=0",(job_id,)).rowcount
            if changed!=1: raise ConflictError('job is not resumable')
            c.execute('INSERT INTO job_resume_events VALUES(?,?,?,?)',(uuid4().hex,job_id,reason,time.time()))
        return self.get_job(job_id)


def binding_task_id(metadata):
    return str(metadata.get('binding',{}).get('task_id') or metadata.get('binding',{}).get('audit_id') or '')
