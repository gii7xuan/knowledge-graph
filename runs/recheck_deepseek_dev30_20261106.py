"""One explicit same-model recheck after the operator confirmed recharge."""
import json
import multiprocessing
import time
from uuid import uuid4
from runs.preflight_dev30_v63_20261106 import worker
from runs.rerun_dev30_v63_20261106 import OUT,save


def main():
    target=OUT/'deepseek_recharge_check.json'
    assert not target.exists(),'recharge check already performed; no automatic repeat'
    ctx=multiprocessing.get_context('spawn');receiver,sender=ctx.Pipe(duplex=False)
    process=ctx.Process(target=worker,args=(sender,'judge_c',uuid4().hex));process.start();sender.close()
    try:
        result=receiver.recv() if receiver.poll(45) else {'voter_id':'judge_c','response_status':'timeout','outcome_unknown':True}
        result.update(explicit_recharge_recheck=True,synthetic_only=True,automatic_retries=0)
        save(target.name,result)
        previous=json.loads((OUT/'preflight.json').read_text(encoding='utf-8'))
        if not (OUT/'preflight_initial.json').exists():save('preflight_initial.json',previous)
        previous['items']=[result if r['voter_id']=='judge_c' else r for r in previous['items']]
        previous.update(status='ready' if all(r['response_status']=='ok' for r in previous['items']) else 'blocked',
            explicit_recharge_check=target.name,finished_at_epoch=time.time())
        save('preflight.json',previous)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        process.join(timeout=.1)
        if process.is_alive():process.terminate();process.join(timeout=2)
        if process.is_alive():process.kill();process.join(timeout=2)
        receiver.close();process.close()


if __name__=='__main__':main()
