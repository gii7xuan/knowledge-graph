# 新测试候选抽取结果（2026-11-19）

后续对账进展：已找到原请求HTTP 200响应头及正文读取超时的证据，但生成完成与计费仍未知，详见[服务端结果核实记录](timeout_server_verification_20261119.md)。

用户明确授权把14个冻结包发送至DeepSeek接口后，已实际派发2次请求。第2次请求结果未知，按预定规则停止，剩余12包未发送。本轮没有启动五组审核，也没有应用修订、发布本体、导入图谱或生成语义准确率。

## 请求与用量

| 请求 | 北京时间开始 | 结果 | 已知用量 |
|---|---|---|---:|
| 第1包 | 2026-11-19 09:42:53 | HTTP回复已取得；输出达到9000 token，JSON末尾截断，完整回复契约失败 | 输入9312 + 输出9000 = 18312 token |
| 第2包 | 2026-11-19 09:43:51 | 客户端超时，服务端是否完成及是否计费未知 | 未知 |
| 剩余12包 | 未发送 | 按未知请求停止规则阻断 | 没有发生这些请求 |

两笔请求分别为`attempt_4862e5f88d8f4ca9a12a1f12b33d8b62`和`attempt_8373599cfa9a43288473df4a3371a6ce`。SDK与任务层重试均为0，没有重发未知请求。已知用量18312 token不能当成完整账单；没有完整价格材料，费用继续为空。

第2笔回执没有服务端请求ID，现有材料不能证明其未执行。已导出原请求时间、模型、输入绑定和客户端跟踪信息供对账，未联系服务商或发送额外请求。

## 零调用恢复的27条候选

第1份回复虽然整体JSON不完整，但triplets数组前部包含27个完整对象。本轮仅对这些原始完整对象进行本地派生入库：

- 用标准JSON解析器逐个读取完整对象，不补写被截断的第28个对象，不补写缺失的覆盖结论。
- 逐条核对抽取条目schema、原始文段位置及原书引文；27条通过结构与锚点检查。
- 保留每条对象在原始回复中的字符范围、原始片段哈希、原回执哈希及派生来源。原请求仍为failed/invalid_contract，没有改成成功回复。
- 对照已有30条与160条开发材料的同源/重复事实规则，本轮恢复项没有被排除；该规则不等于穷尽所有历史泄漏。

派生候选共27条，均来自第1包所属的一本原书。27条均未绑定正式关系ID，其中1条保留`INCONSISTENT_QUALIFIER_APPLICABILITY`契约问题；没有静默纠正或删除。它们是待审核的模型抽取候选，结构和引文定位检查不代表事实正确或可准入。

没有人工金标准，未计算准入精确率、正确保留率或错误放行率。空白人工标注模板已生成。

主目录的原始抽取汇总与空候选清单保留原样，记录完整回复契约下的0条。27条单独存放在`partial_prefix_recovery`目录，是有明确派生来源的恢复数据，不能将两份汇总误认为同一评价口径。

## 当前状态与下一步

300条正式测试池尚未建成。当前可供检查的是27条派生候选，不是完整、均衡或经过人工裁决的正式测试集。两个原始请求尚有一笔未结清，剩余调用继续停止。

先根据第2笔原请求的服务端证据或迟到回执核实结果与用量。现有停止规则下，不因再次运行脚本而自动重发或继续。后续抽取协议还应缩小单包来源量以降低输出截断风险，保留已生成事实与旧票据；不能靠改写历史回复或提高样本通过率来处理。

恢复脚本完成了完整对象、嵌入括号和不完整对象排除的本地检查；随后对27条派生候选逐一核对原始片段哈希与清单绑定。231个先前冻结的代码文件未变化。没有把本轮通道失败当作抽取事实错误。

## 结果文件

- [27条派生候选](../outputs/formal_experiment_20261119_launch_v2/partial_prefix_recovery/candidates.json)
- [恢复汇总](../outputs/formal_experiment_20261119_launch_v2/partial_prefix_recovery/summary.json)
- [原始完整对象及片段位置](../outputs/formal_experiment_20261119_launch_v2/partial_prefix_recovery/raw_complete_items.json)
- [候选清单与内容哈希](../outputs/formal_experiment_20261119_launch_v2/partial_prefix_recovery/population_manifest.json)
- [空白人工标注](../outputs/formal_experiment_20261119_launch_v2/partial_prefix_recovery/human_annotations_blank.json)
- [原始抽取汇总](../outputs/formal_experiment_20261119_launch_v2/extraction_summary.json)
- [请求对账材料](../outputs/formal_experiment_20261119_launch_v2/request_reconciliation.json)
- [最终核验](../outputs/formal_experiment_20261119_launch_v2/postrun_verification.json)
- [用户授权记录](../outputs/formal_experiment_20261119_launch_v2/extraction_authorization.json)
- [零调用恢复代码](../evaluation/extraction_prefix_recovery.py)
