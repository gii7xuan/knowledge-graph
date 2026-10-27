"""Execute and report all forty acceptance rows, using a real isolated Neo4j."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import unittest
from contextlib import closing
from datetime import datetime,timezone
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from tests.acceptance_catalog import CASES
from scripts.isolated_neo4j import isolated_neo4j


class EvidenceResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs); self.evidence={}; self.started={}
    def startTest(self,test):
        self.started[test.id()]=time.monotonic(); super().startTest(test)
    def record(self,test,status,detail=None):
        self.evidence[test.id()]={'status':status,'duration_seconds':round(time.monotonic()-self.started.get(test.id(),time.monotonic()),3),
            **({'detail':detail} if detail else {})}
    def addSuccess(self,test): self.record(test,'passed'); super().addSuccess(test)
    def addFailure(self,test,err): self.record(test,'failed',self._exc_info_to_string(err,test)); super().addFailure(test,err)
    def addError(self,test,err): self.record(test,'error',self._exc_info_to_string(err,test)); super().addError(test,err)
    def addSkip(self,test,reason): self.record(test,'skipped',reason); super().addSkip(test,reason)
    def addExpectedFailure(self,test,err): self.record(test,'expected_failure',self._exc_info_to_string(err,test)); super().addExpectedFailure(test,err)
    def addUnexpectedSuccess(self,test): self.record(test,'unexpected_success'); super().addUnexpectedSuccess(test)
    def addSubTest(self,test,subtest,err):
        if err: self.record(test,'failed',self._exc_info_to_string(err,test))
        super().addSubTest(test,subtest,err)


def source_link(test_id):
    parts=test_id.split('.'); relative='/'.join(parts[:-2])+'.py'; method=parts[-1]
    path=ROOT/relative
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    lines=[node.lineno for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name==method]
    return {'path':relative,'line':lines[0] if lines else None,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def fingerprint_production():
    from scripts.validate_review_upgrade_v4 import fingerprint
    path=ROOT/'runs/v3/review.sqlite3'
    if not path.exists(): return {'available':False}
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        state=fingerprint(conn)
    catalog=ROOT/'runs/ontology_releases/release_catalog.sqlite3'
    with closing(sqlite3.connect(catalog.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        release=conn.execute('SELECT release_id FROM ontology_release_head WHERE singleton=1').fetchone()[0]
    return {'available':True,'database_logical_sha256':state['logical_sha256'],'row_counts':state['row_counts'],
            'release_id':release,'original_ontology_sha256':hashlib.sha256((ROOT/'ontology/ontology.json').read_bytes()).hexdigest()}


def render_report(report,output):
    lines=['# T01～T40 验收核对与补漏','',f"运行时间：{report['started_at']}。结果：{report['passed']}/40 项通过。",'',
        '验收对象为工程行为与控制流。模型响应为 Mock；图谱场景使用隔离的真实 Neo4j，发布竞争包含独立进程事务。此报告不代表真实模型准确率。','',
        '| 编号 | 场景 | 结果 | 验证要求 |','| --- | --- | --- | --- |']
    for row in report['cases']:
        lines.append(f"| {row['id']} | {row['scenario']} | {row['status']} | {row['expected']} |")
    lines.extend(['','## 可复查的代码与测试',''])
    for row in report['cases']:
        lines.extend([f"### {row['id']}：{row['scenario']}",'',row['expected'],'',
            '代码：'+', '.join(f'[{path}]({(ROOT/path).as_posix()})' for path in row['code']), ''])
        for test in row['test_results']:
            source=test['source']; anchor=f":{source['line']}" if source.get('line') else ''
            lines.append(f"- `{test['status']}` [{test['id']}]({(ROOT/source['path']).as_posix()}{anchor})（{test.get('duration_seconds',0)} 秒）")
        lines.append('')
    lines.extend(['## 本轮补漏','',
        '- T13：本体审核契约升级为 v2；通用约束须明确引用定义、分类或范围证据，instance_only / unknown 不能批准。',
        '- T18：人工拒绝或要求修订扩展时，同一当前订阅转为 schema_unresolved，不产生事实为假的标签。',
        '- T19：影响清单扩展到类层级和关系的传递依赖，冻结候选修订、行版本及内容摘要；发布后为历史候选创建新修订和重审，撤回旧贡献。并发变化保留人工结果，预算耗尽时停用旧授权并明确记录。',
        '- T21：允许人工用新修订、新 release 和新证据包完成发布后重审；仍执行同一来源、Schema 和版本检查，不继承旧票。',
        '- T36：平票明确返回 unresolved，不再偏向第一个最大选项。',
        '- T37：冻结完整候选主表，保留缺失预测、超时、失败和未标注行；覆盖率使用全集，准确性使用标注子集；拒绝重复、外来或版本错配结果。',
        '- 补充实际 Neo4j 测试：别名身份、两个来源跨度聚合、受影响历史贡献撤回，以及时间、范围、量词和可能性保留。','',
        '## 环境与执行边界','',f"- Neo4j：`{report.get('neo4j',{}).get('server','unavailable')}`；测试进程停止：`{report.get('neo4j',{}).get('server_stopped',False)}`。",
        f"- 生产数据库、当前本体与原始本体未变：`{report['production_unchanged']}`。",
        f"- 独立测试数：{report['test_count']}；缺失或跳过测试不会算作验收通过。",'- 不运行真实模型质量实验，不自动迁移生产库或写入生产图谱。','',
        '本轮未覆盖整套阶段 F 实验设计。OWL 扩展发布保留完整来源，支持已审核的七类受限操作；复杂公理保留并阻断未执行部分的自动准入，不声称具备未运行的推理器能力。',''])
    (output/'acceptance_report.md').write_text('\n'.join(lines),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output'); parser.add_argument('--neo4j-home',default='D:/ProgramData/neo4j-community-2025.11.2')
    parser.add_argument('--plan-file'); args=parser.parse_args()
    output=Path(args.output or ROOT/'runs/acceptance'/uuid4().hex).resolve()
    output.mkdir(parents=True,exist_ok=False)
    if [c['id'] for c in CASES]!=[f'T{n:02}' for n in range(1,41)]: raise ValueError('acceptance registry must contain every ID exactly once')
    for case in CASES:
        for path in case['code']:
            if not (ROOT/path).is_file(): raise ValueError('missing implementation evidence: '+path)
    test_ids=list(dict.fromkeys(test for case in CASES for test in case['tests']))
    report={'started_at':datetime.now(timezone.utc).isoformat(),'scope':'plan T01--T40 engineering acceptance',
        'real_model_calls':0,'model_accuracy_evaluated':False,'cases':[],'test_count':len(test_ids)}
    if args.plan_file:
        content=Path(args.plan_file).read_bytes()
        report['plan_file']={'path':str(Path(args.plan_file).resolve()),'sha256':hashlib.sha256(content).hexdigest()}
    before=fingerprint_production(); results={}; graph={}; error=None
    prior_uri=os.environ.get('KG_TEST_NEO4J_URI')
    try:
        with isolated_neo4j(output/'neo4j',args.neo4j_home) as graph:
            # Set the environment before importing modules with skipUnless decorators.
            os.environ['KG_TEST_NEO4J_URI']=graph['uri']
            loader=unittest.TestLoader()
            suite=unittest.TestSuite(loader.loadTestsFromName(test) for test in test_ids)
            if loader.errors: raise ValueError('invalid acceptance selectors: '+str(loader.errors))
            with (output/'unittest.log').open('w',encoding='utf-8') as log:
                result=unittest.TextTestRunner(stream=log,verbosity=2,buffer=True,resultclass=EvidenceResult).run(suite)
                results=result.evidence
    except Exception as exc:
        error=str(exc)
    finally:
        if prior_uri is None: os.environ.pop('KG_TEST_NEO4J_URI',None)
        else: os.environ['KG_TEST_NEO4J_URI']=prior_uri
    report['neo4j']=graph
    report['production_before']=before; report['production_after']=fingerprint_production()
    report['production_unchanged']=report['production_before']==report['production_after']
    if error: report['execution_error']=error
    for case in CASES:
        tests=[{'id':test,**results.get(test,{'status':'not_run'}),'source':source_link(test)} for test in case['tests']]
        passed=all(t['status']=='passed' for t in tests) and (not case['requires_real_neo4j'] or bool(graph.get('server')))
        report['cases'].append({**case,'status':'passed' if passed else 'not_passed','test_results':tests,
            'implementation_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in case['code']}})
    report['passed']=sum(c['status']=='passed' for c in report['cases']); report['not_passed']=40-report['passed']
    report['success']=report['passed']==40 and report['production_unchanged'] and not error
    (output/'acceptance_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    render_report(report,output)
    print(json.dumps({'report':str(output/'acceptance_report.json'),'markdown':str(output/'acceptance_report.md'),
        'passed':report['passed'],'not_passed':report['not_passed'],'test_count':len(test_ids),
        'production_unchanged':report['production_unchanged'],'execution_error':error},ensure_ascii=False,indent=2))
    if not report['success']: raise SystemExit(1)


if __name__=='__main__': main()
