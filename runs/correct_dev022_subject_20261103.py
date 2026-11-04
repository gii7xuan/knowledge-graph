"""Submit the source-bound subject correction through the fixed committee."""
from copy import deepcopy
from importlib.util import spec_from_file_location,module_from_spec
import json
from pathlib import Path
from document.hierarchy_index import load_document_index
from evidence.verification import make_evidence_bundle, verify_evidence_bundle
from review_agent.case_budget import CaseBudget
from review_agent.cli import _configured_validators
from review_agent.consensus import Committee
from review_agent.contracts import sha256_json
from review_agent.jobs import JobRunner
from review_agent.repository import ReviewRepository
from review_agent.revision_service import RevisionService
from review_agent.service import load_config,ReviewService
from review_agent.progress import write_status_snapshot

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/dev30_corrections_20261103'
CID='cand_ca27ef4e78caeece9179'


def dump(name,data):write_status_snapshot(OUT/name,data)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    data=json.loads((ROOT/'outputs/dev30_20261031/sample_data.json').read_text(encoding='utf-8'))
    binding=next(b for b in data['bindings'] if b['sample_id']=='DEV-022')
    candidate=data['selected_raw_candidates'][21];assert sha256_json(candidate)==binding['candidate_content_hash']
    spec=spec_from_file_location('pilot',ROOT/'runs/prepare_dev30_revision_pilot_20261103.py');pilot=module_from_spec(spec);spec.loader.exec_module(pilot)
    pilot.verify_inputs(data);original=pilot.ReadOnly(data['source_database'])
    repo=ReviewRepository(OUT/'development_review.sqlite3');repo.initialize();service=RevisionService(repo)
    if not repo.list_runs():repo.create_run(original.get_run(candidate['run_id']));repo.add_candidate(deepcopy(candidate))
    run=repo.get_run(candidate['run_id']);document=load_document_index(run['document_path'],candidate['source_index_hash'],require_verified=True)
    if not (OUT/'DEV-022_subject_draft.json').exists():
        audit=original.get_audit(CID,binding['audit_id']);state=(audit.get('agent_state') or {}).get('state',{})
        archive={e['evidence_id']:e for p in audit['rounds'] for e in p['evidence_bundle']['items']}
        archive.update({e['evidence_id']:e for e in state.get('archive',[])})
        protected=set(state.get('protected_ids',[]))|{eid for v in audit['votes'] if v.get('response_status')=='ok' for eid in v.get('counter_evidence_ids',[])}
        protected|={eid for issue in audit['context'].get('issue_history',[]) if issue.get('status','open')!='resolved' for eid in issue.get('evidence_ids',[])}
        bundle=verify_evidence_bundle(make_evidence_bundle(archive.values(),['frozen_subject_correction']),document,candidate)
        assert not bundle['rejected_items'] and protected<={e['evidence_id'] for e in bundle['items']}
        evidence=['ev_e6de37594d24ed42f3b2','ev_3c257acf77bccb69eb66']
        changes={'head':'矢量方程（1-56）','head_type':None,'relation_id':None,
                 'qualifiers':{'polarity':'positive'},'qualifier_status':{'polarity':'extracted'}}
        draft=service.propose(candidate,repo.get_candidate(CID)['row_version'],changes,evidence,
            '原文将矢量方程（1-56）投影后得到微分方程组，修正主语为矢量方程（1-56），不把结果充当投影对象。原文是肯定表述，显式核验positive极性，不作默认补值。当前本体没有矢量方程的明确类，旧头类型及投影属性domain指向微分方程组，清除这两个不成立的正式映射，待专门核验；保留直角坐标系尾项、基本假设条件与其余限定。请逐字段核验原文与正式定义。',
            bundle,document,protected_ids=protected,source_audit_id=binding['audit_id'])
        dump('DEV-022_subject_draft.json',draft)
    draft=json.loads((OUT/'DEV-022_subject_draft.json').read_text(encoding='utf-8'))
    config=load_config();voters=_configured_validators(config);committee=Committee([v['id'] for v in voters],required_votes=3,minimum_distinct_families=3)
    if service.draft(draft['draft_id'])['status']=='proposed':
        budget=CaseBudget({'max_llm_calls_per_candidate':3,'max_total_tokens':150000,'total_timeout_seconds':420},repo,[repo.ensure_case(CID)],task_type='candidate_revision_review',task_id=draft['draft_id'])
        result=service.review(draft['draft_id'],voters,committee,budget);dump('DEV-022_revision_review.json',result)
    current=service.draft(draft['draft_id'])
    if current['status']=='review_incomplete' and not (OUT/'DEV-022_format_recovery.json').exists():
        failed=[v for v in current['review_result'].get('votes',[]) if v.get('response_status')!='ok']
        if failed and all(v.get('response_status') in {'invalid_contract','invalid_json','invalid_reference'} and not v.get('outcome_unknown') for v in failed):
            dump('DEV-022_first_pass.json',current['review_result'])
            budget=CaseBudget({'max_llm_calls_per_candidate':len(failed),'max_total_tokens':150000,'total_timeout_seconds':420},repo,[repo.ensure_case(CID)],task_type='candidate_revision_review',task_id=draft['draft_id'])
            result=service.review(draft['draft_id'],voters,committee,budget);dump('DEV-022_revision_review.json',result);dump('DEV-022_format_recovery.json',{'attempted':True,'status':result['status']})
    current=service.draft(draft['draft_id']);print(json.dumps({'revision_review':current['status']},ensure_ascii=False),flush=True)
    manual_file=OUT/'DEV-022_user_revision.json'
    if current['status']=='needs_human' and not manual_file.exists():
        event=json.loads((OUT/'human_correction_event.json').read_text(encoding='utf-8'))
        assert event['subject_correction']['after']=='矢量方程（1-56）'
        votes=current['review_result']['votes']
        assert len(votes)==3 and all(v['response_status']=='ok' and any(f['field']=='head' and f['status']=='supported' for f in v['field_reviews']) for v in votes)
        before=repo.get_revision(CID);assert before['revision']==1 and before['triple']['head']==event['subject_correction']['before']
        # This is the user's explicit manual data correction, not an automatic
        # committee approval. The valid objection and draft status are retained.
        with repo.transaction(immediate=True) as connection:
            updated=repo.revise_candidate(CID,repo.get_candidate(CID)['row_version'],{'triple':draft['changes']},_connection=connection)
            connection.execute('CREATE TABLE IF NOT EXISTS development_user_revisions(event_hash TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,record_json TEXT NOT NULL)')
            record={'user_instruction_event_hash':event['event_hash'],'base_candidate_hash':sha256_json(before),
                'user_confirmed_change':{'head':'矢量方程（1-56）'},
                'consequential_mapping_cleanup':{'head_type':None,'relation_id':None},
                'evidence_grounded_polarity':{'polarity':'positive','status':'extracted'},
                'model_draft_approval_status':current['status'],'valid_model_votes_preserved':True,
                'actor':'explicit_user_instruction','not_a_kg_admission':True}
            connection.execute('INSERT INTO development_user_revisions VALUES(?,?,?)',(event['event_hash'],CID,json.dumps(record,ensure_ascii=False)))
            job_id=repo.enqueue_job_in_transaction(connection,'audit',{'run_id':candidate['run_id'],'candidate_ids':[CID],
                'mode':'shadow','force':False,'user_subject_correction_event':event['event_hash']})
        record.update(candidate=repo.get_revision(CID),reaudit_job_id=job_id,production_applied=False)
        dump(manual_file.name,record)
    if current['status']=='approved':dump('DEV-022_application.json',service.apply(draft['draft_id'],draft['draft_hash']))
    if (service.draft(draft['draft_id'])['status']=='applied' or manual_file.exists()) and not (OUT/'DEV-022_source_job.json').exists():
        config=deepcopy(config);config.update(mode='shadow',database_path=str(repo.database_path));config['ontology_extension']['auto_publish_enabled']=False
        runner=JobRunner(repo,ReviewService(repo,config=config).execute_audit_job,max_workers=1,job_types=['audit'])
        try:job=runner.run_available_once(chain=False)
        finally:runner.shutdown()
        dump('DEV-022_source_job.json',job)
    current=repo.get_revision(CID);decision=repo.latest_decision(CID) or {}
    summary={'sample_id':'DEV-022','candidate':current,'candidate_content_hash':sha256_json(current),
             'revision_draft_status':service.draft(draft['draft_id'])['status'],
             'source_support_result':decision.get('source_support_result'),
             'workflow_state':repo.get_candidate(CID)['workflow_state'],
             'human_confirmed_subject':'矢量方程（1-56）','new_formal_mapping_approved':False,
             'old_votes_reusable':False,'production_applied':False}
    dump('DEV-022_correction_result.json',summary);pilot.verify_inputs(data)
    print(json.dumps({k:summary[k] for k in ('sample_id','revision_draft_status','source_support_result','workflow_state','production_applied')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
