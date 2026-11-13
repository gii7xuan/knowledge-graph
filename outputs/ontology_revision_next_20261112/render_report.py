"""Readable status for the bounded, unapproved candidate bundle."""
from collections import Counter
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def read(name): return json.loads((OUT / name).read_text(encoding='utf-8-sig'))


def main():
    manifest = read('prepared_manifest.json')
    table = ['|批次|操作|原文证据|RDF增加/删除|内容|', '|---|---:|---:|---:|---|']
    for b in manifest['batches']:
        table.append(f"|[{b['batch_id']}]({(OUT / b['batch_id'] / 'prepared_proposal.json').as_posix()})|{b['operation_count']}|{b['evidence_count']}|{b['added_triples']}/{b['removed_triples']}|{b['description']}|")
    text = f'''# 正式定义与结构修订：新隔离候选包

已将上一轮离线结论落实到可编译、可核验的候选差异，准备7批独立提案，共93个操作：89项定义修订、4项约束修订，涉及90个现有ID。每批不超过16个操作，全部基于同一批准基线，不把未批准结果当成下一批正式基线。

这是待审核材料。没有新增模型调用，没有生成正式发布版本，也没有切换生产。

## 已准备的具体修订

|对象|候选改动|处理状态|
|---|---|---|
|空气“消耗”|range从弹丸改为现有动能ID，并补边界层条件定义|已编译候选，未批准|
|分离波“具有部件”|改为分离波→正激波，保留斜激波的来源说明|已编译候选，未批准|
|波动阻力“取决于”|range由弹头部改为现有头部形状ID|已编译候选，未批准|
|惯性参考系|移除从条件例子推广出的“动参考系”父类|只完成父类候选，通用定义仍待处理|
|阻力系数、坐标系|分别澄清系数与力、通用坐标与动坐标的范围|已准备定义候选，未批准|
|空气阻力函数“表示”|限定为相对空气速度对运动影响的相应部分|已准备定义候选，未批准|

19项结构问题中，6项形成完整候选修订，1项部分完成，12项继续暂缓。暂缓项涉及公式或方程、受力对象、计算任务、依赖项等角色，不能用注释代替端点修复，也没有随意新建“公式实例类”。

原80项定义草案中，78项纳入本次准备，2项因既有ID语义复用待定而暂缓；另保留原8项未批准定义并重新冻结，再为3项端点修复补定义，合计89项。89项分为24项类定义与65项关系定义。所有旧票保留为历史，没有转移到这些新提案。

## 批次与本地核验

{chr(10).join(table)}

每批已执行现有RDF编译、结构检查、全基线检索、影响扫描和本地能力检查。自然语言整章问项没有被伪记为本地通过；OWL推理器也没有被宣称已执行。

合并预览保持189个类、81个关系，所有ID、标签均保留。RDF由882条变为951条，精确增加93条、删除24条；删除均可追溯到定义语言修订或明确的命名约束修改。合并预览只用于检查这些互不重叠的有界补丁共同作用后的结果，不是超出16操作上限的单一审核任务，也不是发布库的新基线。

[合并预览RDF]({(OUT / 'INTEGRATED_UNAPPROVED_PREVIEW.ttl').as_posix()}) · [影响扫描]({(OUT / 'integrated_impact.json').as_posix()}) · [结构处置明细]({(OUT / 'structural_disposition.json').as_posix()}) · [复用判断]({(OUT / 'reuse_decisions.json').as_posix()})

## 新整章审核门禁

新提案显式启用`ontology_semantic_competency_v2`。每个请求包含完整冻结章节、完整RDF基线和无损差异，以及全部270个本体ID的清单。每个ID必须返回原文支持、已声明含义、结构/方向/范围三项判断，并引用可复用的原文引文ID。

本地核验会拒绝漏ID、多ID、伪造或不唯一引文、无引文的全通过行，以及未解释的非通过行。完整输出合约的哈希也进入请求绑定；只返回一条总体pass不能算整章覆盖。旧v1提案继续使用原问项、原输出格式和原提示词，旧请求与旧票不改写。

`definition_gaps`单独报告正式定义缺口。缺定义不自动等于概念无来源；语义覆盖通过也不等于定义齐全、建模批准或允许发布。语义和结构有争议的项目仍应返回fail/unknown，不能通过补标签或移植旧票绕过。

[270项完整清单]({(OUT / 'full_coverage_inventory.json').as_posix()}) · [B01整章请求]({(OUT / 'B01/prepared_competency_request.json').as_posix()}) · [B01输出合约]({(OUT / 'B01/coverage_output_contract.json').as_posix()})

## 尚未完成的工作

合并预览中的类定义从153/189增至158/189，关系定义从0/81增至65/81；这些数字都是未批准候选覆盖，生产覆盖未改变。仍有47个定义缺口：31个类、16个关系。结合原6项暂缓、结构问题及复用问题，去重后还有53个ID需要继续处理。

原6项暂缓继续保留：大气分层术语冲突及同温层父类版面疑点、标准定律/标准分布的ID范围、横风影响关系的模型条件和旧未知审核。未找到可用于消解相关排版问题的原书页图，本轮不以常识替代来源核验。

[53项后续工作队列]({(OUT / 'remaining_work_queue.json').as_posix()}) · [47项定义缺口]({(OUT / 'remaining_definition_gaps.json').as_posix()}) · [暂缓原因]({(OUT / 'held_items.json').as_posix()})

下一步先处理这53项的来源、指称和参数角色，再形成后续有界补丁及最终组合审核方案。当前只冻结候选包，不提前认定其满足发布门禁。真实模型累计仍为13次，新增额度为0；后续外部审核需要针对最终材料和次数单独授权，本轮未请求追加调用。

## 校验与保留记录

[最终校验]({(OUT / 'delivery_verification.json').as_posix()}) · [回归结果]({(OUT / 'regression_test_result.json').as_posix()}) · [冻结清单]({(OUT / 'prepared_manifest.json').as_posix()})

候选由AI起草，未计入人工金标准。生产文件、上一轮逐项标注、原DeepSeek有效fail及4条正式未知请求均保留。新隔离库的候选订阅和审核任务已登记，但新任务尚无任何模型票。初始准备快照保存于`draft_snapshot_01`，最终材料细化发生在任何外部发送之前，有独立历史记录。
'''
    (OUT / 'report.md').write_text(text, encoding='utf-8')
    print('Rendered offline bundle report.')


if __name__ == '__main__': main()
