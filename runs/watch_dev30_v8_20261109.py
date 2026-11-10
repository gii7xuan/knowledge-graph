"""Bounded read-only reporting while the V8 development regression is running."""
import json
import time
from runs.report_dev30_v8_20261109 import main as report
from runs.rerun_dev30_v8_20261109 import OUT


def main():
    deadline=time.monotonic()+8*3600
    while time.monotonic()<deadline:
        report()
        summary=json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
        if summary['processing_closed']==30:return
        progress=json.loads((OUT/'progress.json').read_text(encoding='utf-8')) if (OUT/'progress.json').exists() else {}
        if progress.get('paused') or progress.get('status') in {'failed','cancelled','interrupted'}:return
        time.sleep(30)


if __name__=='__main__':main()
