"""Bounded background report generation; never calls models or recovers seats."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import time
import traceback
from review_agent.progress import write_status_snapshot
from runs.report_dev30_v62_20261105 import main as report
from runs.rerun_dev30_v62_20261105 import OUT


def main():
    p=argparse.ArgumentParser();p.add_argument('--interval',type=int,default=30)
    args=p.parse_args();deadline=time.monotonic()+8*3600
    previous=None;errors=0
    while time.monotonic()<deadline:
        try:
            report()
            s=json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
            progress=json.loads((OUT/'progress.json').read_text(encoding='utf-8'))
            state={'updated_at':datetime.now(timezone.utc).isoformat(),'population':30,
                'audits_started':s['audits_started'],'processing_closed':s['processing_closed'],
                'independent_review_completed':s['independent_review_completed'],
                'model_calls_from_this_monitor':0,'production_written':False,
                'report_path':str((OUT/'report.md').resolve()),'report_generation_errors':errors}
            if s['processing_closed']==30:
                state['status']='finished' if s['independent_review_completed']==30 else 'processing_finished_review_incomplete'
            elif progress.get('paused'):
                state['status']='batch_paused';state['pause_reason']=progress.get('pause_reason')
            elif progress.get('status') in {'failed','cancelled','interrupted','completed'} and progress.get('remaining',0)==0:
                # The pilot's progress file may briefly replace the full batch's.
                # Require an unchanged terminal observation for two polls.
                signature=(progress.get('execution_id'),progress.get('status'),s['audits_started'],s['processing_closed'])
                state['status']='batch_stopped_with_remaining_samples' if signature==previous else 'running'
                previous=signature
            elif time.time()-progress.get('updated_at_epoch',time.time())>600:
                state['status']='batch_progress_stale'
            else:
                state['status']='running';previous=None
            write_status_snapshot(OUT/'monitor.json',state)
            if state['status']!='running': return
        except Exception:
            errors+=1;traceback.print_exc()
            if errors>=10:
                write_status_snapshot(OUT/'monitor.json',{'status':'report_generation_failed','errors':errors,'model_calls_from_this_monitor':0})
                return
        time.sleep(max(5,args.interval))
    write_status_snapshot(OUT/'monitor.json',{'status':'monitor_deadline_reached','model_calls_from_this_monitor':0})


if __name__=='__main__':main()
