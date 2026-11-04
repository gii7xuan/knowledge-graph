"""Source-based follow-up, including substantive responses to valid opposition."""
from pathlib import Path
import json
from review_agent.repository import ReviewRepository
from review_agent.proposal_resolver import selected_proposal
from review_agent.revision_patch import merge_triple_patch

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'runs/candidate_revision_resolution_20261030'
repo=ReviewRepository(ROOT/'runs/v3/review.sqlite3');plans=[]


def add(identity,changes,evidence,reason):
    current=repo.get_candidate(identity);candidate=repo.get_revision(identity)
    decision=repo.latest_decision(identity);proposal,_=selected_proposal(repo,candidate,decision)
    audit=repo.get_audit(identity,decision['audit_id']);state=(audit.get('agent_state') or {}).get('state',{})
    archive={item['evidence_id']:item for p in audit['rounds'] for item in p['evidence_bundle']['items']}
    archive.update({item['evidence_id']:item for item in state.get('archive',[])})
    protected=set(state.get('protected_ids',[]))|{eid for vote in audit['votes'] if vote.get('response_status')=='ok' for eid in vote.get('counter_evidence_ids',[])}
    protected|={eid for issue in audit['context'].get('issue_history',[]) if issue.get('status','open')!='resolved' for eid in issue.get('evidence_ids',[])}
    if not set(evidence)<=set(archive) or not protected<=set(archive):raise RuntimeError('missing verified material')
    target=merge_triple_patch(candidate['triple'],changes,strict=True)
    if target==candidate['triple']:raise RuntimeError('no-op correction')
    plans.append({'candidate_id':identity,'base_revision':candidate['revision'],'expected_row_version':current['row_version'],
        'source_audit_id':audit['audit_id'],'source_proposal_digest':proposal['proposal_digest'],'changes':changes,
        'evidence_ids':evidence,'reason':reason,'protected_evidence_ids':sorted(protected),'pilot':len(plans)<3,
        'requires_original_three_model_revision_approval':True,'requires_fresh_new_revision_review':True})


add('cand_11ebaf84d2e6a55f208c',{'tail':'风洞试验或其他类比的方法','qualifiers':{'quantifier':'只能通过经验确定'},'qualifier_status':{'quantifier':'extracted'}},
    ['ev_d3d166c370990b769345'],'将方式和排他限制从尾实体中分离，保留“对于新设计的弹丸”的原条件和 can 模态，不采用非法 can_only 枚举。')
add('cand_52159eb442b708f4e860',{'qualifiers':{'modality':'asserted'}},['ev_e9a494c49acda0dae429','ev_8573465d7fe0f75cec4d'],
    '原文明确断言“总是很小”，保留总是量词和限定对象，纠正误记的 usually 模态。')
for identity in ('cand_5337e39022845e99d9c3','cand_5a479d68254bb2dd4174'):
    add(identity,{'qualifiers':{'modality':'can'}},['ev_13481a8bf4640d52ee93'],'补回原文“能影响”的能力模态；不改变原对象及其他限定。')
add('cand_228d07528dee4371012b',{'head':'一条 Cx0-Ma 曲线','tail':'另一条 Cx0-Ma 曲线','qualifiers':{'condition':'由两个形状相近的弹丸所测出','polarity':'positive','quantifier':'一定的相似性'},
    'qualifier_status':{'condition':'extracted','polarity':'extracted','quantifier':'extracted'}},['ev_b050eb5e49bfdf8b2dd3','ev_c2d7613b65a637d2c9e7'],
    '明确相似性两端为两条曲线，纠正原空气阻力系数与弹丸的论元错位；保留“形状相近”“一定”限制。')
add('cand_43809d045ba9207db5ee',{'head':'落角减小'},['ev_21de0e2a073161c2f485','ev_abf138680e0f9c54082a'],
    '将作为原因的“落角减小”写入主体，避免把有条件的反向关系读成落角增大导致结果增大；保留原射程一定及落角越小条件。')
add('cand_7e053c4ec2d202871b6e',{'surface_relation':'变化趋势','tail':'减小','tail_kind':'literal','tail_type':None,'datatype':'string','value':'减小','relation_id':None},
    ['ev_fe9672c3d073ee3f43ed','ev_56f4387b1ee8bda3865a'],'把原文不及物变化陈述表示为趋势属性，移除 head=tail 的自指二元“减小”；保留升弧和始终限制，关系需另经本体审核。')
add('cand_8105bfac8d5998a43a07',{'surface_relation':'差异程度','tail':'很小','tail_kind':'literal','datatype':'string','value':'很小'},
    ['ev_abb40c2987434043da1a'],'将比较结论表示为字面属性，例外句只留在已有 condition 中，不再作为尾实体。')
add('cand_8556574513f630b88891',{'surface_relation':'占','tail':'空气总阻力','qualifiers':{'condition':'超声速弹丸','quantifier':'约60%'},'qualifier_status':{'condition':'extracted','quantifier':'extracted'}},
    ['ev_6f6af60ef02320380a5d'],'以原文占比关系替代有歧义的“是”，补回超声速适用条件及约数，保留原反对证据供三席核查。')
add('cand_884267726a7c467da665',{'head':'空气密度函数的标准定律 H(y)','head_type':None,'surface_relation':'属于'},
    ['ev_c5a18380fabe21fd0ca2','ev_3ac495284f0e81c6c629'],'将原主语收窄为 H(y) 的标准定律，保留炮兵用范围，撤除不适用于新指称的旧类型；分类关系是否合适仍由原三席核验。')
add('cand_c70843072d01abc2a6d9',{'head':'g0','head_type':None,'surface_relation':'为','tail':'重力加速度的地面值','tail_type':None},
    ['ev_9f2acbcd058c97a1e742'],'按原文还原符号定义方向，不再将修饰成分误作上位实体，不推测符号与新尾项的本体类型。')
add('cand_e1528507157178cdd2eb',{'head':'激波的形成','head_type':None,'qualifiers':{'condition':'当弹丸超声速飞行时'},'qualifier_status':{'condition':'extracted'}},
    ['ev_d70fc77313113dfdc12b'],'改用完整原句明确的因果主体，补回超声速条件，不凭不完整片段假定“弹道波”和“激波”同义。')
add('cand_ee43d432882af8d3a035',{'head':'式（1-100）','head_type':None,'surface_relation':'描述','tail':'横风偏与退电之间的关系','tail_kind':'literal','tail_type':None,'datatype':'string','value':'横风偏与退电之间的关系'},
    ['ev_6ba0637a06582bb04526','ev_a19613b80c564196843b'],'还原原文描述者和描述内容，保留原文/OCR术语“退电”，不擅自校成其他词。')
add('cand_f66a7ded77764ef9880c',{'tail':'地表面与平面的差别','tail_type':None},['ev_4c1ca79920c483c96f39','ev_35ce65d3d2ee4a32dffd','ev_21de0e2a073161c2f485'],
    '纠正直接原文宾语，保持“射程越大”条件，不把另句“射程一定且落角越小”混作本条条件，保留该反对材料待独立核查。')
for identity in ('cand_1fbbeb8170f164a64347','cand_9f1d68a89a00599271f3','cand_cd33157f0f9114f8875b'):
    add(identity,{'surface_relation':'包含分量','relation_id':None,'qualifiers':{'polarity':'positive','modality':'can','quantifier':'三项之和中的一项'},
        'qualifier_status':{'polarity':'extracted','modality':'extracted','quantifier':'extracted'}},['ev_1e2d9a3b5ae59338992d','ev_e1cfb2635d4854b6e4e6'],
        '针对单项不等于整体三项之和的有效反对，实质调整为分量关系和单项范围；原反对票留存，不重试原草案取批准。')
add('cand_c1aa85ef7321c2e83f54',{'tail':'以 C、v0、θ0 和 t 为变量的 x、y、v 的外弹道射表','tail_type':None,
    'qualifiers':{'applicability':'对于高射火炮','polarity':'positive'},'qualifier_status':{'polarity':'extracted'}},['ev_d0aefd173dd6805454d4'],
    '把原文适用对象和射表内容分别放回 applicability 与尾项，完整保留已有变量说明，不删除信息来消除争议。')

(OUT/'followup_revision_plan.json').write_text(json.dumps({'items':plans,'selected':len(plans),'model_calls':0,'candidates_modified':False},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'selected':len(plans),'model_calls':0,'candidates_modified':False},ensure_ascii=False))
