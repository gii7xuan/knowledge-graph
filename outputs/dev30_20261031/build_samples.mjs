import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Z]:)/i,'$1');
const data=JSON.parse(await fs.readFile(path.join(dir,'sample_data.json'),'utf8'));
const wb=Workbook.create();
const font='Microsoft YaHei';
const dark='#243B53',amber='#8A5A10',pale='#FFF2CC';
function columnName(n){let s='';for(n++;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;}
function visualLines(value,width){return String(value??'').split('\n').reduce((n,line)=>n+Math.max(1,Math.ceil([...line].reduce((a,c)=>a+(c.codePointAt(0)>255?2:1),0)/Math.max(6,width-2))),0);}
function wrapDisplay(text,limit=44){
 return String(text).split('\n').map(line=>{
  let parts=[],part='',width=0;
  for(const token of line.match(/[A-Za-z0-9_]+|./gu)??[]){
   const size=[...token].reduce((a,c)=>a+(c.codePointAt(0)>255?2:1),0);
   if(part&&width+size>limit){parts.push(part);part='';width=0;}
   part+=token;width+=size;
  }
  parts.push(part);return parts.join('\n');
 }).join('\n');
}
function sheet(name,title,note,headers,rows,widths,{input=false}={}){
 const s=wb.worksheets.add(name);s.showGridLines=false;
 const last=columnName(headers.length-1),end=5+rows.length;
 s.getRange(`A1:${last}${end}`).format.font={name:font,size:10,color:'#182B3A'};
 s.getRange('A2').values=[[title]];s.getRange('A2').format.font={name:font,size:14,bold:true,color:dark};
 s.getRange('A3').values=[[note]];s.getRange('A3').format.font={name:font,size:10,color:'#526577'};
 s.getRange(`A5:${last}5`).values=[headers];
 s.getRange(`A6:${last}${end}`).values=rows;
 s.tables.add(`A5:${last}${end}`,true,'T'+wb.worksheets.items.length+'Data');
 s.getRange(`A5:${last}5`).format={fill:dark,font:{name:font,size:10,bold:true,color:'#FFFFFF'},wrapText:true,horizontalAlignment:'center',verticalAlignment:'center',rowHeight:34};
 s.getRange(`A6:${last}${end}`).format.wrapText=true;
 s.getRange(`A6:${last}${end}`).format.verticalAlignment='top';
 widths.forEach((w,i)=>s.getRange(`${columnName(i)}1:${columnName(i)}${end}`).format.columnWidth=w);
 rows.forEach((row,i)=>{
   const lines=Math.max(...row.map((v,j)=>visualLines(v,widths[j])));
   s.getRange(`A${i+6}:${last}${i+6}`).format.rowHeight=Math.min(400,Math.max(input?110:30,lines*14+14));
   s.getRange(`A${i+6}:${last}${i+6}`).format.fill=i%2===0?'#F3F6F8':'#FFFFFF';
 });
 s.freezePanes.freezeRows(5);s.freezePanes.freezeColumns(input?2:1);
 return s;
}
const humanHeaders=['样本编号','候选表述与当前类型','候选限定及识别状态','直接引文（完整材料见原文证据表）','来源支持','限定完整','关系方向正确','关系映射正确','本体兼容','任务相关','本体覆盖缺口','事实表述正确','建议修改后的表述','人工备注与依据','标注人','标注日期'];
const humanWidths=[12,49,47,70,15,15,16,16,15,15,16,16,42,42,15,17];
const readableRows=data.human_rows.map(row=>[row[0],wrapDisplay(row[1]),wrapDisplay(row[2]),...row.slice(3)]);
const manual=sheet('人工标注','第一章开发复核样本（30条）','请填写 E:P 黄色空白格。先结合原文独立判断，完成后再看“模型对照”。',humanHeaders,readableRows,humanWidths,{input:true});
manual.getRange('E5:P5').format.fill=amber;
manual.getRange('E6:P35').format.fill=pale;
manual.getRange('E6:P35').format.font.color='#164194';
manual.getRange('P6:P35').setNumberFormat('yyyy-mm-dd');
manual.getRange('E6:E35').dataValidation={rule:{type:'list',values:['支持','反驳','证据不足','无法判断']}};
for(const c of ['F','G','H','I','J','L'])manual.getRange(`${c}6:${c}35`).dataValidation={rule:{type:'list',values:['是','否','不适用','无法判断']}};
manual.getRange('K6:K35').dataValidation={rule:{type:'list',values:['有','无','无法判断']}};

const evidence=sheet('原文证据','原文证据与相邻上下文','来源：《火炮弹道学》第一章。全文坐标从0开始，结束位置不包含在内；长引文按400字符连续拆分。',
 ['样本编号','证据序号','证据ID','片段序号','章节/小节','片段起点','片段终点','逐字原文片段','相邻上下文'],
 data.evidence_rows,[12,10,34,12,35,13,13,82,98]);
evidence.getRange(`F6:G${data.evidence_rows.length+5}`).setNumberFormat('0');

const ontology=sheet('本体参考','当前已发布本体（270个术语）','用于独立检查类型、定义和约束。多条OWL domain/range按同时满足解释；定义空白表示该版本未登记。',
 ['正式ID','术语类别','标签','声明别名','定义/描述','父类ID','domain 类ID','range 类ID/数据类型'],
 data.ontology_rows,[35,21,30,35,85,40,45,45]);
const modelRows=data.model_rows.map(row=>{
 const seats=row[5].split('\n').map(text=>{
  const at=text.indexOf('：'),name=text.slice(0,at),v=JSON.parse(text.slice(at+1));
  return name+'\n响应：'+v.response_status+'\n来源：'+v.source_support+'\n审核：'+v.verdict+'\n本体问题：'+v.schema_issue+'\n理由码：'+(v.reason_codes??[]).join('、');
 });
 return [...row.slice(0,5),...seats,...row.slice(6)];
});
const models=sheet('模型对照','当前机器结果与版本绑定','请在独立人工标注完成后再对照。分组、来源结果与本体检查均为机器记录，人工标签保留在首表。',
 ['样本编号','抽样分组（机器状态）','表达特征（抽样用）','当前来源共识','当前本体检查','审核席位1','审核席位2','审核席位3','候选ID','候选修订','候选内容SHA256','审核ID','轮次','冻结提案SHA256','文档版本','来源索引SHA256','本体发布版本','本体Profile SHA256'],
 modelRows,[12,29,30,19,19,56,56,56,36,12,45,43,10,45,45,45,60,45]);
models.getRange('J6:J35').setNumberFormat('0');models.getRange('M6:M35').setNumberFormat('0');
const instructions=[
 ['用途','30条开发复核样本，用于发现问题和改进流程。正式测试集另行冻结，样本比例不用于推算全章准确率。'],
 ['填写位置','只填写“人工标注”E:P黄色单元格。所有人工判断、修改建议、备注、姓名与日期均已留空。'],
 ['阅读顺序','先看候选和原文证据，必要时检索“本体参考”。独立填完判断后，再查看“模型对照”。'],
 ['来源支持','支持：原文支持该表述；反驳：原文明示相反；证据不足：现有材料不足；无法判断：暂不能下结论。'],
 ['限定完整','核对条件、否定、模态、量词、时间及适用范围。发现遗漏或泛化填“否”，在备注说明原文依据。'],
 ['关系方向正确','核对主语、宾语与关系方向；若不适用选择“不适用”。'],
 ['关系映射正确','核对候选已指定的正式关系ID与原句含义。未指定映射可选“不适用”，是否需要补映射写入备注。'],
 ['本体兼容','根据冻结的已发布本体判断。类型或映射不足以判断时选“无法判断”，原文支持另行填写。'],
 ['任务相关','是否属于本次第一章知识图谱任务范围；与原文是否支持分别判断。'],
 ['本体覆盖缺口','已有正式类型/关系/定义能否表达该事实。先查本体参考中的标签、别名、定义及约束，再判断有无缺口。'],
 ['事实表述正确','综合原文、限定、方向判断事实表述是否正确。本体缺口本身不能证明原文事实错误。'],
 ['建议修改后的表述','确需修改时填写完整表述和限定；无修改需要可留空。原候选及编号保留。'],
 ['留空与无法判断','留空表示尚未填写；“无法判断”表示已经查看但不能判断。不要用空白表示“否”。'],
 ['候选原始状态','“候选未记录”表示字段值为空；方括号中保留原有识别状态。unknown不代表肯定或无条件。'],
 ['原文材料','提供当前冻结提案的全部已核验原文证据，并附每片段前后100字符。请结合原章节复核，直接引文仅供定位。'],
 ['来源范围','《火炮弹道学》第一章规范全文，文档版本与索引摘要见模型对照。没有另行生成或改写引文。'],
 ['抽样方法','三组互斥，各10条。来源支持且本体检查通过组10条；三席完整但来源未形成共识组10条；来源支持但本体待处理组10条。'],
 ['样本排序','组内按章节、关系和表达特征择样，固定seed='+ '20261031-dev30-v1'+'；最终按哈希打散展示顺序。'],
 ['覆盖情况','30个章节/小节位置；14条已有修订、12条带条件、5条带非默认模态、7条带量词、4条字面量（特征可重叠）。'],
 ['冻结信息','候选ID、修订、内容摘要、审核提案和本体版本见模型对照；生成前已逐条核对所选版本与当前库一致。'],
 ['回填方式','填写后保留此文件。后续按样本编号和冻结版本核对、整理实验标签；本文件不会自动修改工作台决定或导入图谱。'],
 ['导出时间（北京时间）',data.created_at_beijing]
];
const guide=sheet('填写说明','填写说明与抽样口径','所有人工作业栏均为空白，选项仅用于统一填写口径。',['项目','说明'],instructions,[29,135]);
manual.tabColor=dark;guide.tabColor='#CBD5E1';
wb.recalculate();
console.log((await wb.inspect({kind:'table',range:'人工标注!E5:P8',include:'values,formulas',tableMaxRows:4,tableMaxCols:12,maxChars:1800})).ndjson);
console.log((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!',options:{useRegex:true,maxResults:10},maxChars:700})).ndjson);
for(const [name,range,file] of [
 ['人工标注','A2:D8','manual_source_preview.png'],['人工标注','E5:P8','manual_inputs_preview.png'],
 ['原文证据','A2:I8','evidence_preview.png'],['本体参考','A2:H9','ontology_preview.png'],
 ['模型对照','A2:H8','model_preview.png'],['填写说明','A2:B12','guide_preview.png']]){
 const image=await wb.render({sheetName:name,range,scale:1,format:'png'});
 await fs.writeFile(path.join(dir,file),new Uint8Array(await image.arrayBuffer()));
 console.log('rendered '+file);
}
const xlsx=await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(path.join(dir,'第一章30条开发样本_人工填写.xlsx'));
console.log('EXPORTED 30 samples; all E6:P35 values blank');
