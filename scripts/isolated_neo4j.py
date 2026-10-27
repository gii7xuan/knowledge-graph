"""A local Neo4j fixture with its own ports, configuration, data and lifetime."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager


@contextmanager
def isolated_neo4j(folder,install='D:/ProgramData/neo4j-community-2025.11.2'):
    folder=Path(folder).resolve(); install=Path(install).resolve()
    if folder.exists(): raise ValueError('Neo4j fixture must use a new dedicated directory')
    java=shutil.which('java')
    if not java or not (install/'lib').is_dir(): raise RuntimeError('local Java/Neo4j unavailable; graph acceptance cannot be marked passed')
    folder.mkdir(parents=True); conf=folder/'conf'; conf.mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    settings=['server.default_listen_address=127.0.0.1','dbms.security.auth_enabled=false','server.http.enabled=false',
        'server.https.enabled=false','server.bolt.enabled=true',f'server.bolt.listen_address=127.0.0.1:{port}',
        f'server.bolt.advertised_address=127.0.0.1:{port}','server.memory.heap.initial_size=256m',
        'server.memory.heap.max_size=512m','server.memory.pagecache.size=128m','db.tx_log.rotation.retention_policy=1 files']
    for key,name in (('data','data'),('logs','logs'),('run','run'),('plugins','plugins'),('import','import'),('transaction.logs.root','transactions')):
        path=folder/name; path.mkdir(); settings.append(f'server.directories.{key}={path.as_posix()}')
    (conf/'neo4j.conf').write_text('\n'.join(settings)+'\n',encoding='utf-8')
    command=[java,'-Xms256m','-Xmx512m','--add-opens=java.base/java.nio=ALL-UNNAMED','--add-opens=java.base/java.io=ALL-UNNAMED',
        '--add-opens=java.base/sun.nio.ch=ALL-UNNAMED','--enable-native-access=ALL-UNNAMED','-cp',str(install/'lib/*'),
        'org.neo4j.server.Neo4jCommunity',f'--home-dir={install}',f'--config-dir={conf}']
    log=(folder/'server.log').open('w',encoding='utf-8')
    process=subprocess.Popen(command,cwd=folder,env={**os.environ,'NEO4J_CONF':str(conf)},stdout=log,stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    report={'uri':f'bolt://127.0.0.1:{port}','pid':process.pid,'data_directory':str(folder/'data'),'production_connection':False}
    try:
        from neo4j import GraphDatabase
        for _ in range(90):
            if process.poll() is not None: raise RuntimeError('isolated Neo4j stopped: '+(folder/'server.log').read_text(encoding='utf-8',errors='replace')[-4000:])
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=1): pass
                with GraphDatabase.driver(report['uri'],auth=None,connection_timeout=1) as driver:
                    driver.verify_connectivity(); report['server']=driver.get_server_info().agent
                break
            except Exception: time.sleep(1)
        else: raise RuntimeError('isolated Neo4j did not become ready')
        yield report
    finally:
        if process.poll() is None: process.terminate()
        try: process.wait(timeout=20)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        log.close(); report['server_stopped']=process.poll() is not None
        (folder/'server_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
