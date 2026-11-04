"""Concrete source-bound expression patches, staged without changing candidates."""
from pathlib import Path
from copy import deepcopy
import json
from review_agent.revision_patch import merge_triple_patch

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'runs/candidate_revision_resolution_20261030'
rows={row['candidate_id']:row for row in json.loads((OUT/'cases.json').read_text(encoding='utf-8'))}
plans=[]


def add(identity,patch,evidence,reason,pilot=False):
    row=rows[identity]
    if not set(evidence)<=set(item['evidence_id'] for item in row['evidence']):raise RuntimeError('source ID missing')
    updated=merge_triple_patch(row['triple'],patch,strict=True)
    if updated==row['triple']:raise RuntimeError('no-op revision is not a resolution')
    plans.append({'candidate_id':identity,'base_revision':row['revision'],'expected_row_version':row['row_version'],
        'source_audit_id':row['audit_id'],'source_proposal_digest':row['selected_proposal_digest'],
        'changes':patch,'evidence_ids':evidence,'reason':reason,'pilot':pilot,
        'protected_evidence_ids':row['protected_evidence_ids'],
        'requires_original_three_model_revision_approval':True,'requires_fresh_new_revision_review':True})


add('cand_fa12ee91bb6b9143721f',{'qualifiers':{'condition':'当 τ0 = τ0N 时'},'qualifier_status':{'condition':'extracted'}},
    ['ev_4847c75d22b05d435576'],'补回原文明确的条件限定，保留原主谓宾、极性与其他限定。',True)
add('cand_616dc443087e9d954c97',{'qualifiers':{'time_scope':'有时'},'qualifier_status':{'time_scope':'extracted'}},
    ['ev_6c213a605c011352788d','ev_9d48a1d130bcb4293ab6'],'补回原文“有时”限定，保留已有方便应用条件及 may 模态。',True)
add('cand_31bd91d40eac020d152d',{'head':'地面坐标系中的 x 轴','qualifiers':{'applicability':'地面坐标系','condition':'顺时针旋转为正'},
    'qualifier_status':{'applicability':'extracted','condition':'extracted'}},['ev_02562fe07f825cf6f130'],
    '补回原文明示的坐标系范围与角度正方向约定；以 condition 承载约定，不新增非法限定字段。',True)
add('cand_4bed293118fa1dec3c29',{'qualifiers':{'condition':'动参考系内没有风'},'qualifier_status':{'condition':'extracted'}},
    ['ev_8812846196d7502ca078'],'补回原文因果句的前置条件，保留“全弹道”量词及原关系方向。')

for identity,evidence in [
    ('cand_1733237c3026d874c587',['ev_4f89e426cf3ce358975f']),
    ('cand_8cf5f9e9984ea5fed5f9',['ev_74b05b997b3417afdec2','ev_7c7fc8d95523e905fea1']),
    ('cand_da733233fbf52b47ffbf',['ev_19bbd49e157627a38bfd','ev_96b4dcfafa637ae61fc5']),
    ('cand_9739cbf20359dcc02ec0',['ev_97f493dd870d815d9c2d','ev_afe5f16461b78a1cfcfe']),
    ('cand_72d33d50e5f8386ffd26',['ev_95a54453777be678f78a'])]:
    add(identity,{'qualifiers':{'polarity':'positive'},'qualifier_status':{'polarity':'extracted'}},evidence,
        '原冻结原文为肯定表述，将未核定的极性提交独立修订审核；保留全部其他限定和原文范围，不据默认值直接提交新版本。')

add('cand_dc554bd0b53a511e1228',{'qualifiers':{'polarity':'positive','quantifier':'常'},'qualifier_status':{'polarity':'extracted','quantifier':'extracted'}},
    ['ev_0b479c3c5748720a3d23'],'保留原有条件，补回原文肯定极性和频率词“常”，不加入其他设计或性能内容。')
for identity in ('cand_9f1d68a89a00599271f3','cand_cd33157f0f9114f8875b'):
    add(identity,{'qualifiers':{'polarity':'positive','modality':'can','quantifier':'三项之和'},
        'qualifier_status':{'polarity':'extracted','modality':'extracted','quantifier':'extracted'}},
        ['ev_1e2d9a3b5ae59338992d','ev_e1cfb2635d4854b6e4e6'],
        '补回“可”模态和原文三项之和范围；本条仅为被列举的一项，是否仍需重构关系由独立委员会核验，不强行批准。')
add('cand_5b459021eb0a3fe91145',{'tail':'空气密度函数的标准定律 H(y)','tail_type':None,
    'qualifiers':{'quantifier':'分别'},'qualifier_status':{'quantifier':'extracted'}},['ev_3ac495284f0e81c6c629'],
    '按原文收窄结果对象，保留“分别”并列语境；撤除原宽泛对象类型，不把原有本体 range 当作新对象类型的来源依据。')
add('cand_ca27ef4e78caeece9179',{'head':'矢量方程（1-56）','head_type':None,
    'qualifiers':{'polarity':'positive'},'qualifier_status':{'polarity':'extracted'}},
    ['ev_e6de37594d24ed42f3b2','ev_3c257acf77bccb69eb66'],
    '按原文修正投影动作主体，保留基本假设条件；撤除原方程组类型，不据关系 domain 猜测新主体类型，保留全部原反证供三席复核。')

(OUT/'revision_plan.json').write_text(json.dumps({'items':plans,'pilot_count':sum(item['pilot'] for item in plans),
    'selected':len(plans),'model_calls':0,'candidates_modified':False},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'selected':len(plans),'pilot_count':sum(item['pilot'] for item in plans),'candidates_modified':False},ensure_ascii=False))
