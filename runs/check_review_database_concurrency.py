"""Exercise real Windows WAL readers and writers before resuming model work."""
import json
import multiprocessing
from pathlib import Path
import time
from review_agent.repository import ReviewRepository

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'runs/review_jobs/chapter1_20261028_review_retry1/concurrency_check.sqlite3'


def worker(path,number,queue):
    try:
        repository=ReviewRepository(path)
        for i in range(80):
            with repository.transaction(immediate=True) as connection:
                connection.execute('INSERT INTO concurrency_probe(worker,ordinal) VALUES(?,?)',(number,i))
            with repository.connect() as connection:
                connection.execute('SELECT count(*) FROM concurrency_probe').fetchone()
        queue.put({'worker':number,'status':'ok'})
    except Exception as exc:
        queue.put({'worker':number,'status':'failed','error_type':type(exc).__name__,
            'sqlite_errorname':getattr(exc,'sqlite_errorname',None),'error':str(exc)})


def main():
    if PATH.exists(): raise FileExistsError('use a fresh diagnostic database')
    repository=ReviewRepository(PATH)
    repository.initialize()
    with repository.transaction(immediate=True) as connection:
        connection.execute('CREATE TABLE concurrency_probe(worker INTEGER,ordinal INTEGER,PRIMARY KEY(worker,ordinal))')
    context=multiprocessing.get_context('spawn')
    queue=context.Queue()
    processes=[context.Process(target=worker,args=(str(PATH),n,queue)) for n in range(2)]
    for process in processes: process.start()
    reads=0
    while any(p.is_alive() for p in processes):
        with repository.connect() as connection: connection.execute('SELECT count(*) FROM concurrency_probe').fetchone()
        reads+=1;time.sleep(.005)
    for process in processes:
        process.join(5)
        assert process.exitcode==0
    results=[queue.get(timeout=2) for _ in processes]
    assert all(r['status']=='ok' for r in results),results
    with repository.connect() as connection:
        count=connection.execute('SELECT count(*) FROM concurrency_probe').fetchone()[0]
        integrity=connection.execute('PRAGMA quick_check').fetchone()[0]
    assert count==160 and integrity=='ok'
    result={'status':'passed','writer_processes':2,'committed_writes':count,'parent_read_connections':reads,
        'integrity':integrity,'worker_results':results}
    (PATH.parent/'concurrency_check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
