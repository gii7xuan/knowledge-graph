# 动态权重最小闭环实施记录

日期：2026-11-16。起始 commit：`016347875f01cc5f0f9f358f8ec48ce3b0d35221`。开始时已跟踪文件无未提交修改。

本轮目标：动态权重实际参与三元组审核的研究建议，并在少量案例上验证共识、复核和最终操作的衔接。原30条只是缩短调试时间的取样，没有固定研究角色或数量要求。

## 实现及边界

- `review_agent/vote_weighting.py` 实现 `equal/static/dynamic`。未配置时沿用 legacy 模式；单独打开旧的 `weighted_quorum_enabled` 不会静默启用一个未实现的策略。
- source、expression、mapping、scope 各自计分。历史可靠性采用开发/训练统计的平滑正确率，样本不足回退相同先验；历史记录同时绑定模型身份和家族，替换模型后回退先验。
- 第一版当前特征为 `check_completion_v1`：该维度必要检查中，有明确状态且记录引用的比例。适配系数为 `0.25 + 0.75 * 完成比例`。这是可核查的完成情况特征，尚不代表自动判断了语义蕴含，也不代表已经学到模型专长。
- 先对三个预定席位的 `r*g` 归一化，再用比例水位法限制单席权重；移除不可用或弃权意见后不重新归一化。默认单席上限0.49、决定阈值0.70、可用质量0.65、至少2个明确意见及2个模型家族。这些是工程初始值，正式实验前需要开发数据验证并冻结。
- 有效实质反对和关键反证保留为核验义务。只有弃权、非决定性未知，或在冻结政策下等价的范围类别，不再机械地触发全体一致要求。
- 共识、问题记录、定向复核、resolution快照、Agent结束状态、服务/监控报告、实验预测使用同一权重策略。定向复核依据真实复核席位更新临时观察，原票保留。
- 规划器接收精简的权重、得分、检查缺口和快照身份，完整票及复核历史保存在服务端，避免新增权重快照反复膨胀规划输入。
- 每轮导出 `weight_snapshot`，包含策略/可靠性/特征版本、输入哈希、各席位r/g/w/m、分维度得分、可用质量、有效人数和反证。策略进入 `committee_policy_hash`，从而参与proposal身份、请求缓存及校准兼容性。
- `research_operation` 为 `accept/revise/discard/manual_review`；其研究建议与生产 `decision` 分开。生产校准、发布和导入保护保留。固定候选实验将 `revise` 映射为 `defer` 并另存建议，不计为修复成功。
- 已确认的范围排除可以产生discard；事实被反驳时默认优先提出修订。尚未新增“不可修复事实”的独立判定器，也没有开启实际语义修订、动态本体发布或在线可靠性学习。

## 配置

将 `config/vote_weighting.example.json` 的对象放入审核配置的 `vote_weighting` 键。正式模型配置 `config/review_agent.json` 未切换默认模式。

有历史人工对照时，可靠性记录格式如下；`correct/evaluated` 必须来自对应维度的真实人工标签，不使用系统自身结果或多数票作为真值。

```json
{
  "data_version": "development_labels_v1",
  "partition": "development",
  "models": {
    "judge_a": {
      "model_identity": "actual-canonical-model-id",
      "model_family": "actual-family",
      "axes": {"source": {"correct": 18, "evaluated": 20}}
    }
  }
}
```

实验组支持 `weighting_mode`，例如 `{"id":"agent_static","method":"agent","weighting_mode":"static"}`。固定流程同样可选择该字段，其他模型基线使用各自既定聚合器。

## 可复现检查命令

在 `D:\knowledge-huopao` 的 PowerShell 中执行：

```powershell
$weightingPython = 'D:\miniconda3\envs\knowledge\python.exe'
$env:PYTHONDONTWRITEBYTECODE = '1'
& $weightingPython -B scripts/run_weighting_checks.py
```

运行器仅为Windows沙箱创建继承工作区权限的唯一临时目录，保留原有测试断言和清理。直接使用Python的0700临时目录在本机导致部分SQLite测试无法打开文件。

对已存原票零调用回放，输出路径必须是新文件：

```powershell
& $weightingPython -B -m evaluation.vote_weighting_replay --database outputs/agent_resolution_pilot_20261114_r2/review.sqlite3 --limit 3 --output outputs/vote_weighting_20261116/replay_new.json
```

回放先按原proposal校验原票，再对同一批已验证观察替换权重策略，明确记录原proposal与派生聚合身份；不伪造新票，也不将历史意见重新标成新策略的模型调用。

一个真实候选的隔离Agent冒烟：不带 `--run` 仅准备，带 `--run` 才调用模型。每次必须使用新输出目录；最多8个规划/委员请求、零自动重试、1轮初审、240秒审核预算，实际消耗与未知请求单独记录。

```powershell
& $weightingPython -B -m evaluation.weighting_smoke --output outputs/vote_weighting_20261116/new_live --sample-id DEV-006 --run
```

该配置关闭实际修订、本体执行、案例记忆和生产导入。已生成的准备目录不能用来自动恢复或重复发送未知请求。

## 验证结果

- 扩展回归：209项，0失败、0错误，22项因未提供独立真实Neo4j实例跳过。日志：`outputs/vote_weighting_20261116/checks.log`。最终调整后针对权重、Agent规划、可观测性和复核的100项检查全部通过，其中17项为本轮新增机制检查；日志：`outputs/vote_weighting_20261116/final_checks.log`。
- 已有真实审核记录回放：3个审核记录，等权/静态/动态均为manual_review，动态与静态操作差异为0。记录本体与候选材料限制，不据此宣称权重带来质量提升。最终回放：`outputs/vote_weighting_20261116/replay_final.json`。
- 真实隔离Agent运行：`DEV-006`，6个请求；2个规划请求和3个委员请求完成，最后一个规划请求返回网关504，按结果未知保留，不自动重发。已知输入78828、输出3653 token，另1笔用量未知；费用未推算为0。
- 三席原票均有效，实际source权重约为0.3457/0.3457/0.3086，支持质量0.6914未达0.70；expression质量0.7018，scope纳入，mapping材料不完整。候选还有必要限定信息未明确，最终manual_review，没有强行补全字段或放行。
- 原始候选哈希未变、隔离导入队列为0。记录：`outputs/vote_weighting_20261116/live_dev006/report.json`、`diagnosis.json`。该次执行代码按哈希核对并保存在同目录 `execution_code.zip`。之后的范围排除独立于映射、规划视图压缩调整由受控检查验证，未额外重发真实请求。

没有人工标签的位置保留空值，机制验收不作为论文准确率。真实运行的处理过程结束不等于所有请求已对账，网关504仍是明确的技术未完成项。

工程用例中，两席明确支持、第三席对证据充分性与反证检查弃权：等权/静态支持质量为2/3，未达0.70；动态支持质量约0.718，四维条件齐备时产生accept研究建议。相同有效反证案例仍阻断。这个用例证明权重参与决策，不证明该参数在自然数据上更准确。

进入预实验还需要：实际开发人工标签、历史可靠性快照或明确冷启动设置、证据质量特征的人工对照验证、开发阈值选择，以及五组方法的同池结果和全部成本。
