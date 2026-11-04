import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';

const dir=path.dirname(fileURLToPath(import.meta.url));
const plan=JSON.parse(await fs.readFile(path.join(dir,'workbook_patches.json'),'utf8'));
const source=await fs.readFile(plan.source_workbook);
if(createHash('sha256').update(source).digest('hex')!==plan.source_sha256)throw new Error('Source workbook changed');
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(plan.source_workbook));
console.log((await wb.inspect({kind:'region',sheetId:'人工标注',range:'E10:M17',maxChars:1100,tableMaxRows:8,tableMaxCols:9})).ndjson);
const preview=process.argv.includes('--preview');
if(!preview){
  // Empty shared strings can import as their original shared-string index.
  // Restore only cells verified empty in the untouched input, through public APIs.
  const blankCells=JSON.parse(await fs.readFile(path.join(dir,'import_blank_restore.json'),'utf8'));
  for(const item of blankCells)wb.worksheets.getItem(item.sheet).getRange(item.cell).values=[['']];
  for(const patch of plan.patches){
    const cell=wb.worksheets.getItem(patch.sheet).getRange(patch.cell);
    const actual=cell.values[0][0]??null;
    if(actual!==patch.before)throw new Error(`Unexpected original value at ${patch.cell}: ${actual}`);
    cell.values=[[patch.after]];
  }
  wb.recalculate();
  for(const patch of plan.patches){
    if(wb.worksheets.getItem(patch.sheet).getRange(patch.cell).values[0][0]!==patch.after)throw new Error(`Patch failed: ${patch.cell}`);
  }
}
for(const [range,name] of [['E10:M17','labels'],['M25:N28','subject_note']]){
  const png=await wb.render({sheetName:'人工标注',range,scale:1,format:'png'});
  await fs.writeFile(path.join(dir,`${preview?'before':'after'}_${name}.png`),new Uint8Array(await png.arrayBuffer()));
}
if(!preview){
  const output=await SpreadsheetFile.exportXlsx(wb);await output.save(plan.output_workbook);
  console.log('Exported corrected copy; original candidate/evidence/model bindings preserved.');
}
