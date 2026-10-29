# 第一章候选审核恢复任务

范围为已有的 320 条来源候选，沿用已授权的 Qwen、Gemma、DeepSeek 三模型配置。

前次执行在 SQLite 连接阶段中断；完整性检查通过，已保留数据库备份和 2 条中断审核历史。当前执行使用有限连接重试，失败连接及时关闭。

- [实时状态](status.json)
- [标准输出](stdout.log)
- [错误日志](stderr.log)
- [恢复记录](recovery_record.json)

审核结果持续写回 runs/v3/review.sqlite3，完成后导出 review_summary.json 和 exports/。
