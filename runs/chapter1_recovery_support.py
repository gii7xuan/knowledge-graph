"""Recover audited source responses without trusting stale source or release bindings."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

from document.hierarchy_index import load_document_index
from document.snapshot import json_hash
from extract_triplets.candidate_contract import RESPONSE_SCHEMA


def atomic_json(path,data,*,replace=None,sleep=time.sleep):
    import os
    replace=replace or os.replace
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    for attempt in range(6):
        try:
            replace(temporary,path)
            return
        except PermissionError:
            if attempt==5: raise
            sleep(min(0.05*(2**attempt),0.5))


def progress_json(path,data):
    """The independent fsynced request journal is authoritative, not this UI file."""
    try:
        atomic_json(path,data)
        return True
    except OSError as exc:
        fallback=path.parent/'progress_fallback'
        try:
            fallback.mkdir(exist_ok=True)
            snapshot=fallback/(str(time.time_ns())+'_'+uuid4().hex+'.json')
            snapshot.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:
            pass
        print(json.dumps({'warning':'progress_write_deferred','error_type':type(exc).__name__,
            'path':str(path)},ensure_ascii=False),flush=True)
        return False


def scope_key(scope): return json_hash(scope)


class SourceResponseReplay:
    def __init__(self,checkpoint,*,prompt,release_binding,model,filter_policy):
        self.checkpoint=Path(checkpoint).resolve()
        self.index=load_document_index(self.checkpoint/'source_index.json',require_verified=True)
        self.prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()
        self.release_binding=release_binding
        self.model=model
        self.filter_policy=filter_policy
        self.finished={}
        self.complete={}
        self.complete_scopes=[]
        state=json.loads((self.checkpoint/'status.json').read_text(encoding='utf-8'))
        assert state['source_index_hash']==self.index['index_content_hash']
        for key in ('ontology_release_id','ontology_version','profile_hash'):
            if state[key]!=release_binding[key]: raise ValueError('checkpoint release binding changed: '+key)
        text=self.index['source_snapshot']['canonical_text']
        for line in (self.checkpoint/'requests.jsonl').read_text(encoding='utf-8').splitlines():
            row=json.loads(line)
            if row.get('event')!='finished': continue
            source=row['request_payload'];scope=row['scope']
            if row['model_requested']!=model or row['prompt_hash']!=self.prompt_hash:
                raise ValueError('checkpoint model or prompt differs')
            if row['request_hash']!=json_hash([prompt,source]): raise ValueError('checkpoint request hash mismatch')
            if source['document_version']!=self.index['document_version'] or source['document_id']!=self.index['document_id']:
                raise ValueError('checkpoint document binding mismatch')
            if source['scope']!=scope or source['core_text']!=text[scope['core_start']:scope['core_end']]:
                raise ValueError('checkpoint core text mismatch')
            if source['context_text']!=text[scope['context_start']:scope['context_end']]:
                raise ValueError('checkpoint context text mismatch')
            if source['filter_policy']!=filter_policy or source['output_schema']!=RESPONSE_SCHEMA:
                raise ValueError('checkpoint extraction policy or contract changed')
            if source['ontology_release']!={k:release_binding[k] for k in ('ontology_release_id','ontology_version','profile_hash')}:
                raise ValueError('checkpoint request ontology binding mismatch')
            key=scope_key(scope)
            self.finished.setdefault(key,[]).append(row)
            if row.get('status')=='completed' and row.get('finish_reason')!='length':
                payload=json.loads(row['raw_response'])
                if payload.get('coverage_status')=='complete_for_supplied_scope' and isinstance(payload.get('triplets'),list):
                    self.complete[key]=row
        self.complete_scopes=[r['scope'] for r in self.complete.values()]

    def lookup(self,scope,*,index,model,settings):
        if index['index_content_hash']!=self.index['index_content_hash'] or model!=self.model:
            raise ValueError('replay source or model differs from checkpoint')
        if settings.get('filter_policy')!=self.filter_policy:
            raise ValueError('replay source policy differs from checkpoint')
        for key in ('ontology_release_id','ontology_version','profile_hash'):
            if settings['release_binding'][key]!=self.release_binding[key]: raise ValueError('replay release changed')
        key=scope_key(scope)
        if key in self.complete:
            row=self.complete[key]
            payload=json.loads(row['raw_response'])
            selected=[row]
            coverage='complete_for_supplied_scope'
            triplets=payload['triplets']
        else:
            descendants=[s for s in self.complete_scopes if s['source_chunk_id']==scope['source_chunk_id']
                and scope['core_start']<=s['core_start'] and s['core_end']<=scope['core_end'] and s!=scope]
            selected=self.finished.get(key,[])
            if not descendants or not selected or any(r['status']!='failed' for r in selected): return None
            # Replay historical parent failures to visit the successful child
            # scopes. Missing leaves still make real requests on this run.
            coverage='failed';triplets=[]
        records=[]
        for old in selected:
            record=deepcopy(old)
            record.pop('event',None)
            record.update(cached=True,replayed_from_checkpoint=str(self.checkpoint),
                original_usage=record.get('usage'),usage={'prompt_tokens':0,'completion_tokens':0,'total_tokens':0},
                original_latency_ms=record.get('latency_ms'),latency_ms=0)
            records.append(record)
        return {'triplets':triplets,'coverage_status':coverage,'attempts':records,'prompt_hash':self.prompt_hash}
