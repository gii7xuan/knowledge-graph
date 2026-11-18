import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const out=path.dirname(fileURLToPath(import.meta.url));
const pilot=path.join(out,'pilot');
const {samples}=JSON.parse(await fs.readFile(path.join(pilot,'review_samples.json'),'utf8'));
const schema=JSON.parse(await fs.readFile(path.join(out,'workbook_schema.json'),'utf8'));
const wb=Workbook.create();
const entry=wb.worksheets.add('填写');
const candidates=wb.worksheets.add('候选');
const evidence=wb.worksheets.add('证据');
const guide=wb.worksheets.add('说明');
const font='Arial';
const safe=value=>typeof value==='string'&&value.startsWith('=')?"'"+value:value;
const json=value=>JSON.stringify(value);
function put(sheet,rows,widths){
  rows=rows.map(row=>row.map(safe));
  sheet.getRangeByIndexes(0,0,rows.length,rows[0].length).values=rows;
  const used=sheet.getRangeByIndexes(0,0,rows.length,rows[0].length);
  used.format.font={name:font,size:11,color:'#202936'};
  used.format.verticalAlignment='top';
  used.format.wrapText=true;
  sheet.showGridLines=false;
  sheet.getRangeByIndexes(0,0,1,rows[0].length).format={fill:'#233E58',font:{name:font,size:11,bold:true,color:'#FFFFFF'},rowHeight:32,verticalAlignment:'center',horizontalAlignment:'center',wrapText:true};
  for(let c=0;c<widths.length;c++)sheet.getRangeByIndexes(0,c,rows.length,1).format.columnWidth=widths[c];
  sheet.freezePanes.freezeRows(1);
  return rows;
}
const entryRows=[schema.columns.map(c=>c.header),...samples.map(s=>[s.sample_id,
  `${s.candidate.triple.head} → ${s.candidate.triple.surface_relation} → ${s.candidate.triple.tail}`,
  ...Array(schema.columns.length-2).fill(null)])];
put(entry,entryRows,[16,58,20,21,20,21,23,23,30,30,55,45,20,24,20,24,24]);
entry.tabColor='#233E58';
entry.freezePanes.freezeColumns(2);
entry.getRange(`A2:Q${samples.length+1}`).format.rowHeight=90;
entry.getRange(`C2:Q${samples.length+1}`).format.fill='#FFF2CC';
entry.getRange(`O2:O${samples.length+1}`).setNumberFormat('0.0');
entry.getRange(`N2:N${samples.length+1}`).setNumberFormat('yyyy-mm-dd hh:mm');
for(let c=0;c<schema.columns.length;c++){
  const field=schema.columns[c].field;
  const values=schema.dimensions[field]??(['full_bundle_reviewed','saw_model_conclusions'].includes(field)?['是','否']:null);
  if(values)entry.getRangeByIndexes(1,c,samples.length,1).dataValidation={rule:{type:'list',values}};
}
entry.getRange(`O2:O${samples.length+1}`).dataValidation={rule:{type:'decimal',operator:'greaterThanOrEqual',formula1:0}};
const candidateHeaders=['sample_id','candidate_id','revision','candidate_content_hash','proposal_digest','evidence_hash',
  'head','surface_relation','tail','qualifiers','qualifier_status','semantic_context','formal_relation_id','head_type','tail_type',
  'source_group','source_section','original_source_anchors'];
put(candidates,[candidateHeaders,...samples.map(s=>{const c=s.candidate,t=c.triple,b=s.binding;return [s.sample_id,b.candidate_id,b.revision,
  b.candidate_content_hash,b.proposal_digest,b.evidence_hash,t.head,t.surface_relation,t.tail,json(t.qualifiers),json(t.qualifier_status),json(t.semantic_context),
  t.relation_id,t.head_type,t.tail_type,s.source_group_id,s.source_section,json(s.original_source_anchors.map(a=>({
    evidence_id:a.evidence_id,parent_source:path.basename(a.parent_path),start:a.start,end:a.end})))];})],
  [16,34,12,36,36,36,45,35,55,65,55,95,35,30,30,35,45,100]);
candidates.freezePanes.freezeColumns(1);
for(let r=0;r<samples.length;r++){
  const t=samples[r].candidate.triple;
  const approximateLines=Math.max(8,Math.ceil(json(t.semantic_context).length/86));
  candidates.getRangeByIndexes(r+1,0,1,18).format.rowHeight=Math.min(360,18*approximateLines+18);
}
const evidenceRows=[['样本编号','证据ID','逐字原文','来源小节','原书与原文位置']];
for(const s of samples)for(const e of s.evidence_bundle.items){
  const parent=s.original_source_anchors.find(a=>a.evidence_id===e.evidence_id);
  const source=parent?`${path.basename(parent.parent_path)} · 字符 [${parent.start}, ${parent.end})`:'冻结来源片段；完整原书定位见候选页 original_source_anchors';
  evidenceRows.push([s.sample_id,e.evidence_id,e.quote,s.source_section,source]);
}
put(evidence,evidenceRows,[16,32,180,42,78]);
evidence.freezePanes.freezeColumns(2);
for(let r=1;r<evidenceRows.length;r++){
  const text=String(evidenceRows[r][2]);
  const lineCount=text.split('\n').reduce((n,line)=>n+Math.max(1,Math.ceil([...line].reduce((k,ch)=>k+(ch.charCodeAt(0)>255?2:1),0)/174)),0);
  evidence.getRangeByIndexes(r,0,1,5).format.rowHeight=Math.max(42,Math.min(409,18*lineCount+12));
}
const guideRows=[['项目','填写说明'],
 ['任务','40 条开发试标。每名审核人另存一份模板，独立填写，不查看他人的原标。'],
 ['填写位置','填写页黄色区域可填。样本编号和原候选不改；来源、限定信息及绑定信息保留在候选和证据页。'],
 ['原候选','判断展示的原命题，修改建议另记。尚未绑定正式关系时保留 unassigned，不据此否定来源。'],
 ['空白与未知','空白表示尚未作答；只有人工明确选择 unresolved 才表示已判断但仍待确认。'],
 ['证据引用','填写本条证据页实际 evidence_id，多个 ID 用分号隔开。额外核证材料另存并说明，不伪造原包 ID。'],
 ['模态规范','区分可能性、能力/许可、假设、近似和定义性陈述。保留近似含义，不因出现“可”机械改为能力，也不自动将 can 改为 asserted。'],
 ['实际用时','逐条记录阅读、核证和填写所用秒数。未知留空，实际零值可保留。金标准建设工时与部署审核工时分开。'],
 ['人工裁决','两份原标及分歧分别保存。表格转换只生成待裁决原始声明，不会自动成为金标准。'],
 ['使用范围','开发池来自两本书，英文候选保持英文。来源切片不算独立测试文档，语言和本体覆盖限制单列。'],
 ['完整规范','见同目录 README.md；逐条阅读可用 cards.html。'],
 ...Object.entries(schema.dimensions).map(([field,values])=>[field,values.join(' / ')]),
 ['来源标签释义','supported 支持原命题；contradicted 有反驳依据；insufficient 材料不足；unresolved 尚有实质疑点。'],
 ['表达标签释义','faithful 保留来源含义；needs_revision 需修改；unresolved 含义仍待判断。'],
 ['映射标签释义','correct 正确；incorrect 错误；unassigned 未赋正式 ID；ambiguous 多义；unresolved 待确认。'],
 ['操作偏好释义','include 纳入；revise 修改；exclude 排除；unresolved 待确认。操作偏好独立记录，不倒推事实标签。']];
put(guide,guideRows,[30,115]);
guide.freezePanes.unfreeze();
for(let r=1;r<guideRows.length;r++)guide.getRangeByIndexes(r,0,1,2).format.rowHeight=guideRows[r][1].length>85?66:48;
wb.recalculate();
const inspection=await wb.inspect({kind:'table',range:'填写!A1:H4',include:'values,formulas',tableMaxRows:4,tableMaxCols:8,maxChars:2500});
await fs.writeFile(path.join(out,'workbook_inspection.ndjson'),inspection.ndjson);
const errors=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:20},maxChars:1500});
await fs.writeFile(path.join(out,'workbook_error_scan.ndjson'),errors.ndjson);
for(const [sheetName,range] of [['填写','A1:H5'],['候选','G1:K3'],['证据','A1:C3'],['说明','A1:B7']]){
  const blob=await wb.render({sheetName,range,scale:1,format:'png'});
  await fs.writeFile(path.join(out,`preview_${sheetName}.png`),new Uint8Array(await blob.arrayBuffer()));
}
const file=await SpreadsheetFile.exportXlsx(wb);
const output=path.join(pilot,'人工试标模板.xlsx');
await file.save(output);
console.log(JSON.stringify({output,samples:samples.length,sheets:4,labels_prefilled:0,evidence_rows:evidenceRows.length-1}));
