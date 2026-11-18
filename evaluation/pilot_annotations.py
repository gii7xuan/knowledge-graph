"""Read blank/filled pilot workbooks as pending raw claims, never automatic gold."""
import argparse
import hashlib
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from review_agent.annotation_contract import DIMENSIONS
from review_agent.contracts import sha256_json

COLUMNS=[('样本编号','sample_id'),('原候选','statement'),('来源支持','source_support'),
    ('表达质量','expression_status'),('已有映射','mapping_status'),('本体覆盖','schema_coverage'),
    ('知识范围','knowledge_scope'),('操作偏好','admission_preference'),('支持证据ID','supporting_evidence_ids'),
    ('反驳证据ID','counter_evidence_ids'),('理由与待核问题','note'),('修改建议','expected_revision_text'),
    ('审核人','reviewer'),('标注时间','annotated_at'),('实际用时（秒）','duration_seconds'),
    ('已核对完整材料','full_bundle_reviewed'),('看过模型审核结论','saw_model_conclusions')]
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def workbook_rows(path,sheet_name):
    """Read the supported flat template with stdlib; preserve blanks and typed zero."""
    with ZipFile(path) as z:
        wb=ET.fromstring(z.read('xl/workbook.xml'))
        target=next(s for s in wb.findall('s:sheets/s:sheet',NS) if s.attrib['name']==sheet_name)
        rid=target.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
        rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        relative=next(r.attrib['Target'] for r in rels if r.attrib['Id']==rid)
        name=relative.lstrip('/') if relative.startswith('/') else 'xl/'+relative
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            strings=[''.join(si.itertext()) for si in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        result=[]
        for row in ET.fromstring(z.read(name)).findall('s:sheetData/s:row',NS):
            values={}
            for cell in row.findall('s:c',NS):
                if cell.find('s:f',NS) is not None:raise ValueError('formula cells are not permitted in raw annotation records')
                column=''.join(x for x in cell.attrib['r'] if x.isalpha());index=0
                for ch in column:index=index*26+ord(ch)-64
                value=cell.find('s:v',NS);kind=cell.attrib.get('t');value=value.text if value is not None else None
                if kind=='s':value=strings[int(value)] if value is not None else None
                elif kind=='inlineStr':value=''.join(cell.find('s:is',NS).itertext())
                elif kind=='b':value=value=='1'
                elif kind not in ('str','e') and value is not None:
                    value=float(value);value=int(value) if value.is_integer() else value
                values[index-1]=value
            if any(v not in (None,'') for v in values.values()):
                result.append([values.get(i) for i in range(max(values,default=-1)+1)])
        return result


def raw_record(values,template,sample):
    row={field:values[i] if i<len(values) else None for i,(_,field) in enumerate(COLUMNS)}
    if row['sample_id']!=template['sample_id']:raise ValueError('unknown or reordered sample identity')
    t=sample['candidate']['triple']
    if row['statement']!=f'{t["head"]} → {t["surface_relation"]} → {t["tail"]}':raise ValueError('displayed original candidate was modified')
    result=deepcopy(template);raw=result['raw_annotation']
    for key,allowed in DIMENSIONS.items():
        value=row[key]
        if value not in (None,'') and value not in allowed:raise ValueError('invalid annotation '+key)
        raw['labels'][key]=None if value=='' else value
    available={e['evidence_id'] for e in sample['evidence_bundle']['items']}
    for key in ('supporting_evidence_ids','counter_evidence_ids'):
        value=row[key]
        ids=[] if value in (None,'') else [x.strip() for x in str(value).replace('；',';').replace(',',';').replace('\n',';').split(';') if x.strip()]
        if not set(ids)<=available:raise ValueError('annotation evidence outside frozen sample')
        raw[key]=list(dict.fromkeys(ids))
    for key in ('note','expected_revision_text','reviewer'):
        raw[key]='' if row[key] is None else str(row[key])
    stamp=row['annotated_at']
    if isinstance(stamp,(int,float)) and not isinstance(stamp,bool):
        stamp=(datetime(1899,12,30,tzinfo=timezone(timedelta(hours=8)))+timedelta(days=stamp)).isoformat()
    raw['annotated_at']=None if stamp in (None,'') else str(stamp)
    duration=row['duration_seconds']
    if duration not in (None,'') and (isinstance(duration,bool) or not isinstance(duration,(float,int)) or not math.isfinite(duration) or duration<0):
        raise ValueError('duration_seconds must be a nonnegative number or blank')
    raw['duration_seconds']=None if duration=='' else duration
    for key in ('full_bundle_reviewed','saw_model_conclusions'):
        value=row[key]
        if value not in (None,'','是','否') and type(value) is not bool:raise ValueError('invalid review provenance '+key)
        raw[key]=None if value in (None,'') else value in (True,'是')
    # Workbook entries remain original raw claims, even if every field is filled.
    result['adjudication']['adjudication_status']='pending'
    return result


def convert(workbook,pilot,reviewer='A'):
    read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
    samples=read(pilot/'review_samples.json');blank=read(pilot/('reviewer_'+reviewer+'_blank.json'))
    from .manifest import validate_manifest
    manifest=validate_manifest(read(pilot/'manifest.json'))
    if samples['manifest_hash']!=manifest['manifest_hash'] or blank['manifest_hash']!=manifest['manifest_hash']:
        raise ValueError('annotation manifest binding mismatch')
    templates={r['sample_id']:r for r in blank['annotations']};packets={r['sample_id']:r for r in samples['samples']}
    for identity,sample in packets.items():
        if sample['binding']!=templates[identity]['binding'] or sha256_json(sample['candidate'])!=sample['binding']['candidate_content_hash']:
            raise ValueError('frozen annotation package candidate binding mismatch')
    bindings=workbook_rows(workbook,'候选')
    expected=['sample_id','candidate_id','revision','candidate_content_hash','proposal_digest','evidence_hash']
    if bindings[0][:6]!=expected:raise ValueError('candidate binding headers changed')
    found=set()
    for row in bindings[1:]:
        identity=row[0]
        if identity in found or identity not in templates:raise ValueError('duplicate or foreign candidate binding')
        found.add(identity);b=templates[identity]['binding']
        if any(row[i]!=(identity if i==0 else b[key]) for i,key in enumerate(expected)):
            raise ValueError('candidate binding was modified')
        sample=packets[identity];t=sample['candidate']['triple']
        source_values=[t['head'],t['surface_relation'],t['tail']]
        if row[6:9]!=source_values or [json.loads(row[i]) for i in (9,10,11)]!=[
                t['qualifiers'],t['qualifier_status'],t.get('semantic_context')]:
            raise ValueError('displayed candidate fields were modified')
        if row[12:17]!=[t.get('relation_id'),t.get('head_type'),t.get('tail_type'),sample['source_group_id'],sample['source_section']]:
            raise ValueError('displayed mapping or source fields were modified')
        parent_spans=[{'evidence_id':a['evidence_id'],'parent_source':Path(a['parent_path']).name,
            'start':a['start'],'end':a['end']} for a in sample['original_source_anchors']]
        if json.loads(row[17])!=parent_spans:raise ValueError('parent source locations were modified')
    if found!=set(templates):raise ValueError('candidate binding table incomplete')
    evidence=workbook_rows(workbook,'证据');expected_evidence={(s['sample_id'],e['evidence_id']):e['quote']
        for s in samples['samples'] for e in s['evidence_bundle']['items']}
    shown={(r[0],r[1]):r[2] for r in evidence[1:]}
    if shown!=expected_evidence or len(shown)!=len(evidence)-1:raise ValueError('displayed evidence was modified or incomplete')
    rows=workbook_rows(workbook,'填写')
    if rows[0]!=[header for header,_ in COLUMNS]:raise ValueError('annotation headers changed')
    result=[];seen=set()
    for values in rows[1:]:
        identity=values[0]
        if identity in seen or identity not in templates:raise ValueError('duplicate or foreign annotation sample')
        seen.add(identity);result.append(raw_record(values,templates[identity],packets[identity]))
    if seen!=set(templates):raise ValueError('annotation population incomplete')
    return {'manifest_hash':blank['manifest_hash'],'reviewer_role':'reviewer_'+reviewer,
        'annotations':result,'annotation_status':'raw_claims_pending_human_adjudication',
        'automatic_gold_created':False,'source_workbook_hash':hashlib.sha256(Path(workbook).read_bytes()).hexdigest()}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workbook',type=Path,required=True)
    p.add_argument('--pilot',type=Path,required=True);p.add_argument('--reviewer',choices=['A','B'],default='A');p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();result=convert(args.workbook,args.pilot,args.reviewer)
    with args.output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'raw_annotations':len(result['annotations']),'automatic_gold_created':False}))


if __name__=='__main__':main()
