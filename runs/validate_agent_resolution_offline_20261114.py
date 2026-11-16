"""Export four synthetic Mock workflow proofs; these are not real model quality results."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from evaluation.review_quality import bind_packet,quality_report
from review_agent.contracts import sha256_json
from runs.regress_v8 import ROOT,fingerprints
from tests.test_agent_resolution import ResolutionRuntimeTests
from tests.test_resolution_workflows import ResolutionWorkflowTests


def run(directory):
    directory=Path(directory).resolve()
    if not directory.is_relative_to(ROOT/'outputs') or directory.exists():
        raise ValueError('use a new output directory under outputs')
    directory.mkdir(parents=True);packets=[];summaries=[]
    for mode in ('source_closure','mapping','revision','handoff'):
        case=(ResolutionRuntimeTests('runTest') if mode=='source_closure' else ResolutionWorkflowTests('runTest'))
        case.setUp()
        try:
            if mode=='source_closure':case.execute_source_closure();fixture=case.f.fixture
            else:case.execute_case(mode);fixture=case.f
            material=deepcopy(case.last_case);result=material['final'];cid=material['candidate']['candidate_id']
            audit=fixture.repo.get_audit(cid,result['audit_id']);candidate=fixture.repo.get_revision(cid)
            proposal=next(p for p in audit['rounds'] if p['proposal_digest']==result['selected_proposal_digest'])
            context=audit['context'];bundle=context.get('resolution_bundle') or proposal['evidence_bundle']
            system={k:result['review_outcome'].get(k) for k in ('recommendation','review_completed','semantic_status',
                'technical_status','technical_incomplete','end_to_end_incomplete','required_human_action')}
            packet=bind_packet(candidate,proposal,bundle,proposal['review_scope_policy'],system=system,
                               resolution_snapshot=result.get('resolution_snapshot'))
            packets.append(packet)
            target=directory/mode;target.mkdir()
            from evaluation.agent_resolution import revision_review_materials
            revision_packets,operations=revision_review_materials(fixture.repo,{cid},[packet])
            case.assertEqual(len(operations),int(mode in {'mapping','revision'}))
            revision_quality=quality_report(revision_packets,revision_operations=operations,shadow_revision_operations=operations)
            metric=next(m for m in revision_quality['metrics'] if m['id']=='revision_success_rate')
            case.assertIsNone(metric['value']);case.assertEqual(metric['unknown_count'],len(operations))
            (target/'revision_evaluation.json').write_text(json.dumps({'samples':revision_packets,'operations':operations,
                'quality_report':revision_quality},ensure_ascii=False,indent=2),encoding='utf-8')
            with fixture.repo.connect() as source,sqlite3.connect(target/'review.sqlite3') as destination:source.backup(destination)
            material.update(final_audit=audit,final_candidate=candidate,formal_ontology=deepcopy(fixture.base.ontology),
                            dataset_kind='synthetic_mock',not_real_model_quality_validation=True)
            (target/'workflow.json').write_text(json.dumps(material,ensure_ascii=False,indent=2),encoding='utf-8')
            summaries.append({'case':mode,**system,'original_votes_unchanged':material['original_votes_unchanged'],
                              'actual_new_model_calls':0,'human_labels':0})
        finally:case.doCleanups()
    report={'version':'synthetic_resolution_workflow_validation_v1','dataset_kind':'synthetic_mock',
        'code_hash':sha256_json(fingerprints()),'cases':summaries,'real_model_calls':0,'human_labels':0,
        'quality_improvement_established':False,'real_development_and_independent_validation_pending':True}
    quality=quality_report(packets)
    for name,value in [('workflow_report.json',report),('review_packets.json',{'samples':packets}),('quality_report.json',quality)]:
        (directory/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    result=run(parser.parse_args().output)
    print(json.dumps({'dataset_kind':result['dataset_kind'],'cases':len(result['cases']),'real_model_calls':0,'human_labels':0}))
