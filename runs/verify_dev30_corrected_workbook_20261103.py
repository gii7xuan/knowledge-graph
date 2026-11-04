"""Read-only semantic/native-feature comparison of the corrected workbook copy."""
import hashlib,json,zipfile,xml.etree.ElementTree as E
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'outputs/dev30_corrections_20261103'
plan=json.loads((OUT/'workbook_patches.json').read_text(encoding='utf-8'))
NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def load(path):
    output={}
    with zipfile.ZipFile(path) as z:
        strings=[''.join(x.itertext()) for x in E.fromstring(z.read('xl/sharedStrings.xml')).findall('s:si',NS)] if 'xl/sharedStrings.xml' in z.namelist() else []
        rel={r.get('Id'):r.get('Target') for r in E.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        for sh in E.fromstring(z.read('xl/workbook.xml')).find('s:sheets',NS):
            target=rel[sh.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
            path=target.lstrip('/') if target.startswith('/') else 'xl/'+target;root=E.fromstring(z.read(path));cells={}
            for c in root.findall('s:sheetData/s:row/s:c',NS):
                v=c.find('s:v',NS)
                if c.get('t')=='s' and v is not None:value=strings[int(v.text)]
                elif c.get('t')=='inlineStr':value=''.join(c.find('s:is',NS).itertext())
                else:value=v.text if v is not None else None
                if value is not None:cells[c.get('r')]=value
            output[sh.get('name')]={'cells':cells,'panes':[x.attrib for x in root.findall('.//s:pane',NS)],
                'validations':[(x.attrib,[(y.tag,y.text) for y in x]) for x in root.findall('s:dataValidations/s:dataValidation',NS)],
                'row_heights':[(x.get('r'),x.get('ht')) for x in root.findall('s:sheetData/s:row',NS) if x.get('ht')],
                'col_widths':[(x.get('min'),x.get('max'),x.get('width')) for x in root.findall('s:cols/s:col',NS)]}
        tables=[E.fromstring(z.read(n)).attrib for n in z.namelist() if n.startswith('xl/tables/table') and n.endswith('.xml')]
    return output,tables


before,bt=load(plan['source_workbook']);after,at=load(plan['output_workbook'])
assert list(before)==list(after)
allowed={(x['sheet'],x['cell']):x['after'] for x in plan['patches']};changes=[];viewport_changes=[];empty_serialization_changes=[]
for name in before:
    for cell in set(before[name]['cells'])|set(after[name]['cells']):
        a=before[name]['cells'].get(cell);b=after[name]['cells'].get(cell)
        if a!=b and a in (None,'') and b in (None,''):
            empty_serialization_changes.append({'sheet':name,'cell':cell,'before':a,'after':b})
            continue
        if a!=b:
            assert (name,cell) in allowed,(name,cell,a,b)
            assert b==allowed[(name,cell)];changes.append({'sheet':name,'cell':cell,'before':a,'after':b})
    # Saved scroll positions are viewport state, not the frozen row/column split.
    a=[{k:v for k,v in p.items() if k!='topLeftCell'} for p in before[name]['panes']]
    b=[{k:v for k,v in p.items() if k!='topLeftCell'} for p in after[name]['panes']]
    assert a==b,(name,'frozen panes')
    if before[name]['panes']!=after[name]['panes']:
        viewport_changes.append({'sheet':name,'before':before[name]['panes'],'after':after[name]['panes']})
    for key in ('validations','col_widths'):assert before[name][key]==after[name][key],(name,key)
    a=dict(before[name]['row_heights']);b=dict(after[name]['row_heights'])
    assert a.keys()==b.keys() and all(abs(float(a[k])-float(b[k]))<1e-5 for k in a),(name,'row_heights')
assert len(changes)==len(allowed)==6
assert bt==at
assert hashlib.sha256(Path(plan['source_workbook']).read_bytes()).hexdigest()==plan['source_sha256']
proof={'changed_cells':sorted(changes,key=lambda x:x['cell']),'all_other_cell_values_unchanged':True,
       'sheet_order':list(after),'table_count':len(at),'tables_freeze_splits_validations_dimensions_preserved':True,
       'row_height_numeric_tolerance_points':1e-5,
       'empty_cells_semantically_compared':True,
       'empty_serialization_change_count':len(empty_serialization_changes),
       'saved_viewports_reset_by_export':viewport_changes,
       'original_sha256':plan['source_sha256'],'corrected_sha256':hashlib.sha256(Path(plan['output_workbook']).read_bytes()).hexdigest(),
       'original_modified':False}
(OUT/'workbook_verification.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'changed_cells':len(changes),'all_other_values_unchanged':True,'native_tables_preserved':len(at),
                  'original_modified':False},ensure_ascii=False))
