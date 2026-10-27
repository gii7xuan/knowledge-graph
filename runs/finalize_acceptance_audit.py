import hashlib
import json
from pathlib import Path
from scripts.validate_acceptance_t01_t40 import fingerprint_production

root=Path(__file__).resolve().parents[1]
folder=root/'runs/acceptance/d26a94721b074d04a70e3e5273f8782a'
report=json.loads((folder/'acceptance_report.json').read_text(encoding='utf-8'))
regression=json.loads((root/'runs/acceptance_regression.json').read_text(encoding='utf-8'))
assert report['success'] and report['passed']==40 and regression['success']
for case in report['cases']:
    assert all(test['status']=='passed' for test in case['test_results'])
    for name,digest in case['implementation_sha256'].items():
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest,name
    for test in case['test_results']:
        source=test['source']
        assert hashlib.sha256((root/source['path']).read_bytes()).hexdigest()==source['sha256'],source['path']
production=fingerprint_production()
assert production==report['production_before']==report['production_after']
config=json.loads((root/'config/review_agent.json').read_text(encoding='utf-8'))
summary={'acceptance_passed':40,'acceptance_total':40,'acceptance_unique_tests':report['test_count'],'acceptance_skipped':0,
    'full_regression':regression,'frontend_tests_passed':9,'frontend_build':'passed','source_hashes_match_tested_versions':True,
    'neo4j':report['neo4j'],'production_unchanged_after_regression':True,'production_fingerprint':production,
    'mode':config['mode'],'auto_accept_enabled':config['routing']['auto_accept_enabled'],
    'auto_import_enabled':config['neo4j']['auto_import_enabled'],'real_model_calls':0,'model_accuracy_evaluated':False,
    'acceptance_report':str(folder/'acceptance_report.json')}
(folder/'completion_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
text=(folder/'acceptance_report.md').read_text(encoding='utf-8')
text+='\n## 全量回归与最终复核\n\n'
text+=f"- 40/40 验收通过，共 {report['test_count']} 个独立专项测试，无跳过项。\n"
text+=f"- 全量回归 {regression['tests']} 项：{regression['tests']-regression['skipped_real_neo4j_tests']} 项通过、{regression['skipped_real_neo4j_tests']} 项因默认未启用 Neo4j 跳过；本次专项验收已在真实隔离 Neo4j 中验证核心图谱场景。\n"
text+='- 前端 9 项测试通过，Vite 构建通过。\n- 已再次检查代码和测试源文件摘要，均与专项执行时一致。\n- 四个已知测试生成文件已恢复，生产数据库、当前 release 和原始本体摘要保持不变。\n'
text+=f"- 当前配置为 `{config['mode']}`，自动接纳 `{config['routing']['auto_accept_enabled']}`、自动图谱导入 `{config['neo4j']['auto_import_enabled']}`。\n\n"
text+=f"[完整 JSON 验收记录]({(folder/'acceptance_report.json').as_posix()}) · [最终汇总]({(folder/'completion_summary.json').as_posix()}) · [全量回归日志]({(root/'runs/acceptance_regression.log').as_posix()})\n"
text+='\n## 复现入口\n\n```powershell\npython -m scripts.validate_acceptance_t01_t40 --neo4j-home D:/ProgramData/neo4j-community-2025.11.2\n```\n\n'
text+='该入口创建新的隔离目录并重新执行所有映射测试；缺少真实 Neo4j、用例缺失、失败或跳过都不会得到 40/40 通过。可用 `--plan-file` 记录输入方案摘要。\n'
target=root/'docs/acceptance_t01_t40_20261027.md'
if target.exists(): raise RuntimeError('preserving an existing report; select a new document path')
target.write_text(text,encoding='utf-8')
print(json.dumps({'document':str(target),'summary':str(folder/'completion_summary.json'),'passed':40,
    'tests':report['test_count'],'regression_tests':regression['tests'],'production_unchanged':True},ensure_ascii=False,indent=2))
