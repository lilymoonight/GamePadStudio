"""Run every test module in a fresh process so Qt test lifetimes stay local.

Old UI fixtures share a QApplication and leave deferred deletion events behind.
Separate interpreters also isolate SDL virtual devices and native platform mocks.
No tests are omitted; every subprocess exit code is checked.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


def main():
    # Windows CI redirects stdout through a legacy code page even when child
    # pytest processes emit UTF-8 diagnostics containing Chinese test data.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    files = sorted((project / 'tests').glob('test_*.py'))
    reports = Path(tempfile.mkdtemp(prefix='gps-platform-tests-'))
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', SDL_VIDEODRIVER='dummy',
                       SDL_AUDIODRIVER='dummy', PYGAME_HIDE_SUPPORT_PROMPT='1', PYTHONUTF8='1')

    def run(path):
        report = reports / (path.stem + '.xml')
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', str(path), '-q', '--tb=short',
             '--basetemp=' + str(reports / path.stem), '--junitxml=' + str(report)],
            cwd=project, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace')
        (reports / (path.stem + '.log')).write_text(result.stdout, encoding='utf-8')
        counts = dict(tests=0, failures=0, errors=0, skipped=0)
        if report.is_file():
            for suite in ET.parse(report).getroot().iter('testsuite'):
                for key in counts:
                    counts[key] += int(suite.get(key, 0))
        return path, result, counts

    totals = dict(tests=0, failures=0, errors=0, skipped=0)
    failed = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.jobs, 4))) as pool:
        futures = [pool.submit(run, path) for path in files]
        for future in as_completed(futures):
            path, result, counts = future.result()
            for key in totals:
                totals[key] += counts[key]
            print(('PASS ' if result.returncode == 0 else 'FAIL ') + path.name, flush=True)
            if result.returncode:
                failed.append(path.name)
                print(result.stdout[-6000:], flush=True)
    totals['passed'] = totals['tests'] - totals['failures'] - totals['errors'] - totals['skipped']
    summary = dict(totals, modules=len(files), failed_modules=failed, reports=str(reports))
    (reports / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
