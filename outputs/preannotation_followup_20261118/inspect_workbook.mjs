import fs from 'node:fs/promises';
import {FileBlob,SpreadsheetFile} from '@oai/artifact-tool';
const input='C:/Users/P-Pxuan/Downloads/人工试标_AI预标_40条.xlsx';
const wb=await SpreadsheetFile.importXlsx(await FileBlob.load(input));
const view=await wb.inspect({kind:'table',range:'填写!A1:Q5',include:'values,formulas',tableMaxRows:5,tableMaxCols:17,tableMaxCellChars:110,maxChars:4500});
await fs.writeFile('source_workbook_inspection.ndjson',view.ndjson);
const image=await wb.render({sheetName:'填写',range:'A1:K5',scale:1,format:'png'});
await fs.writeFile('source_workbook_preview.png',new Uint8Array(await image.arrayBuffer()));
console.log(JSON.stringify({read_only:true,workbook_exported:false,source:input}));
