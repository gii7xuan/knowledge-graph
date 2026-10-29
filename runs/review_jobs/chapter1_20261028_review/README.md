# 第一章候选审核任务

范围：run_chapter1_20261028_resume1 中的 320 条待审核候选。

使用当前有界审核 Agent，Qwen 规划，Qwen、Gemma、DeepSeek 三模型委员；每个候选最多 240 秒，两个候选并行，按冻结本体和来源证据审核。

- [实时状态](status.json)
- [标准输出](stdout.log)
- [错误日志](stderr.log)
- [模型连接检查](model_probe.json)
- [外发授权记录](destination_authorization.json)

审核过程和结果持续写入 runs/v3/review.sqlite3。完成后生成 review_summary.json、batch_result.json 和 exports/。

本轮处理已有候选，不覆盖原抽取报告中仍未补齐的 7 个片段。

## 后续恢复执行

本次执行已因 SQLite 连接错误中断；完整性检查正常，后续由 [恢复任务](../chapter1_20261028_review_retry1/README.md) 继续。
