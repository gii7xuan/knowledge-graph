"""Launch an isolated native Neo4j and run transaction regressions without production writes."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--neo4j-home',default='D:/ProgramData/neo4j-community-2025.11.2')
    args = parser.parse_args()
    install = Path(args.neo4j_home).resolve()
    folder = ROOT / 'runs/graph_validation' / uuid4().hex
    folder.mkdir(parents=True)
    conf = folder / 'conf'; conf.mkdir()
    port = free_port()
    lines = ['server.default_listen_address=127.0.0.1','dbms.security.auth_enabled=false',
        'server.http.enabled=false','server.https.enabled=false','server.bolt.enabled=true',
        f'server.bolt.listen_address=127.0.0.1:{port}',f'server.bolt.advertised_address=127.0.0.1:{port}',
        'server.memory.heap.initial_size=256m','server.memory.heap.max_size=512m','server.memory.pagecache.size=128m',
        'db.tx_log.rotation.retention_policy=1 files']
    for key, name in (('data','data'),('logs','logs'),('run','run'),('plugins','plugins'),('import','import'),('transaction.logs.root','transactions')):
        path = folder / name; path.mkdir()
        lines.append(f'server.directories.{key}={path.as_posix()}')
    (conf / 'neo4j.conf').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    uri = f'bolt://127.0.0.1:{port}'
    java = shutil.which('java')
    command = [java,'-Xms256m','-Xmx512m','--add-opens=java.base/java.nio=ALL-UNNAMED',
        '--add-opens=java.base/java.io=ALL-UNNAMED','--add-opens=java.base/sun.nio.ch=ALL-UNNAMED',
        '--enable-native-access=ALL-UNNAMED','-cp',str(install / 'lib/*'),
        'org.neo4j.server.Neo4jCommunity',f'--home-dir={install}',f'--config-dir={conf}']
    env = dict(os.environ,NEO4J_CONF=str(conf))
    log = (folder / 'server.log').open('w',encoding='utf-8')
    process = subprocess.Popen(command,cwd=folder,env=env,stdout=log,stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    report = {'scope':'isolated native Neo4j; no production connection','uri':uri,'data_directory':str(folder/'data'),'pid':process.pid}
    try:
        from neo4j import GraphDatabase
        for attempt in range(90):
            if process.poll() is not None: raise RuntimeError('isolated Neo4j stopped: '+(folder/'server.log').read_text(encoding='utf-8',errors='replace')[-6000:])
            try:
                with GraphDatabase.driver(uri,auth=None,connection_timeout=1) as driver:
                    driver.verify_connectivity()
                    report['server'] = driver.get_server_info().agent
                break
            except Exception:
                time.sleep(1)
        else: raise RuntimeError('isolated Neo4j did not become ready')
        os.environ['KG_TEST_NEO4J_URI'] = uri
        suite = unittest.defaultTestLoader.loadTestsFromName('tests.test_admission_graph_sync')
        result = unittest.TextTestRunner(verbosity=2,buffer=True).run(suite)
        report.update(tests_run=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),success=result.wasSuccessful())
    finally:
        # Only our Popen child is stopped, never the installed service/default database.
        process.terminate()
        try: process.wait(timeout=20)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        log.close()
        report['server_stopped'] = process.poll() is not None
        (folder/'validation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    if not report.get('success'): raise SystemExit(1)


if __name__=='__main__': main()
