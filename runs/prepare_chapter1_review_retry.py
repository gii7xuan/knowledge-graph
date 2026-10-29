"""Retain the interrupted audit history and prepare a fresh review execution."""
import json
import argparse
from pathlib import Path
import shutil
import time

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'runs/review_jobs/chapter1_20261028_review'
NEW=OLD.parent/'chapter1_20261028_review_retry1'


def main():
    global OLD,NEW
    parser=argparse.ArgumentParser()
    parser.add_argument('--old')
    parser.add_argument('--new')
    args=parser.parse_args()
    if args.old: OLD=Path(args.old).resolve()
    if args.new: NEW=Path(args.new).resolve()
    if OLD.parent!=ROOT/'runs/review_jobs' or NEW.parent!=OLD.parent: raise ValueError('invalid review job location')
    from review_agent.repository import ReviewRepository
    from review_agent.progress import write_status_snapshot
    if NEW.exists(): raise FileExistsError('retry job already exists; inspect instead of overwriting')
    repository=ReviewRepository(ROOT/'runs/v3/review.sqlite3')
    with repository.connect() as connection:
        integrity=connection.execute('PRAGMA quick_check').fetchone()[0]
        if integrity!='ok': raise ValueError('database integrity check failed')
        rows=connection.execute("SELECT a.audit_id FROM audit_runs a JOIN candidates c ON c.candidate_id=a.candidate_id WHERE c.run_id=? AND a.status='running'",('run_chapter1_20261028_resume1',)).fetchall()
        votes=connection.execute('SELECT count(*) FROM validator_votes').fetchone()[0]
        import sqlite3
        with sqlite3.connect(OLD/'after_interruption.sqlite3') as backup: connection.backup(backup)
    for row in rows:
        repository.finish_incomplete_audit(row['audit_id'],'interrupted',
            {'error_type':'SQLiteOperationalError','error':'disk I/O error during connection setup; worker process exited',
             'recovery':'fresh bounded execution; incomplete history and case budget retained'})
    old=json.loads((OLD/'status.json').read_text(encoding='utf-8'))
    old.update(status='interrupted',active=[],interrupted=old.get('interrupted',0)+len(rows),
        interruption={'error_type':'SQLiteOperationalError','error':'disk I/O error','detected_at_epoch':time.time()},
        resumed_by=NEW.name)
    write_status_snapshot(OLD/'status.json',old)
    NEW.mkdir()
    for name in ('review_config.json','preflight.json','model_probe.json','destination_authorization.json'):
        shutil.copy2(OLD/name,NEW/name)
    write_status_snapshot(NEW/'recovery_record.json',{'previous_job':str(OLD),'closed_interrupted_audits':[r['audit_id'] for r in rows],
        'database_integrity':'ok','candidate_count':320,'previous_validator_votes':votes,
        'database_backup':str(OLD/'after_interruption.sqlite3'),'fresh_execution':True,
        'connection_setup_retry_limit':4})
    (NEW/'README.md').write_text('# 第一章候选审核恢复任务\n\n范围为已有的 320 条来源候选，沿用已授权的 Qwen、Gemma、DeepSeek 三模型配置。\n\n'
        '前次执行在 SQLite 连接阶段中断；完整性检查通过，已保留数据库备份和 2 条中断审核历史。当前执行使用有限连接重试，失败连接及时关闭。\n\n'
        '- [实时状态](status.json)\n- [标准输出](stdout.log)\n- [错误日志](stderr.log)\n- [恢复记录](recovery_record.json)\n\n'
        '审核结果持续写回 runs/v3/review.sqlite3，完成后导出 review_summary.json 和 exports/。\n',encoding='utf-8')
    print(json.dumps({'job':str(NEW),'database_integrity':integrity,'closed_interrupted_audits':len(rows),'previous_validator_votes':votes},ensure_ascii=False))


if __name__=='__main__': main()
