# 第一章候选抽取任务

状态见 [status.json](status.json)。标准输出见 [stdout.log](stdout.log)，错误日志见 [stderr.log](stderr.log)。

- 输入：钱林方《火炮弹道学》第一章，共 55505 个规范字符、135 个来源片段。
- 固定发布版本：release_c722d176f283335f3f398e3559da5ead968973a870efdea663359669db07b6bb
- 任务编号：run_chapter1_20261027_published
- 采用新版来源级候选 v4；仅抽取概念、定义、分类和描述性关系。
- 所有来源片段均纳入计划；模型调用预算为 572，失败、重试与未完成范围均记录。
- 运行检查点：runs/v3/run_chapter1_20261027_published.pending/，请求流水 requests.jsonl。
- 完成后输出：runs/v3/run_chapter1_20261027_published/，包括 extraction_report.json 和 exports/candidates.json；候选登记在 runs/v3/review.sqlite3。
- 本任务仅生成待审核候选，不表示已经审核通过。

远程服务及当前连接设置已获用户明确授权。

## 2026-10-28 恢复记录

原任务已中断，13 次成功响应已离线恢复为 33 条来源候选。剩余抽取已由 [恢复任务](../chapter1_20261028_resume1/README.md) 继续，当前状态见 [恢复任务状态](../chapter1_20261028_resume1/status.json)。
