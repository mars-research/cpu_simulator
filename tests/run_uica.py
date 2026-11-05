#!/usr/bin/env python3
"""Runs uiCA (+ memory model) on every test and compares with ground_truth.json.

    python3 run_uica.py [--arch EMR] [--set nFB=25 ...] [--tests ...] [--tag name]

--set overrides memory-model parameters (memsys.PRESETS keys) for this run; the results go to
results/<tag>.json and a table is printed (error = predicted / measured - 1).
"""
import argparse, json, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
UICA = HERE.parent / 'uiCA.py'
PY = HERE.parent / '.venv' / 'bin' / 'python'
sys.path.insert(0, str(HERE))
import measure


def run(name, arch, overrides):
    spec = json.loads((HERE / 'specs' / f'{name}.json').read_text())
    if overrides:
        spec['config'] = dict({'preset': spec['config']}, **overrides)
    with tempfile.TemporaryDirectory() as d:
        sp, mj = Path(d) / 'spec.json', Path(d) / 'mem.json'
        sp.write_text(json.dumps(spec))
        kernel = measure.TESTS[name][0]
        p = subprocess.run([str(PY), str(UICA), str(HERE / 'build' / f'{kernel}.o'), '-arch', arch, '-TPonly',
                            '-memSpec', str(sp), '-memJson', str(mj)], capture_output=True, text=True)
        if p.returncode:
            return name, {'error': p.stderr[-500:]}
        return name, json.loads(mj.read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--arch', default='EMR')
    ap.add_argument('--set', nargs='*', default=[], help='memory-model overrides, e.g. nFB=25')
    ap.add_argument('--tests', nargs='+', default=list(measure.TESTS))
    ap.add_argument('--tag', default=None)
    args = ap.parse_args()
    ov = {k: float(v) if '.' in v else int(v) for k, v in (a.split('=') for a in args.set)}
    gt = json.loads((HERE / 'ground_truth.json').read_text())
    with ThreadPoolExecutor(8) as ex:
        res = dict(ex.map(lambda t: run(t, args.arch, ov), args.tests))
    tag = args.tag or ('default' if not ov else '_'.join(f'{k}{v}' for k, v in ov.items()))
    (HERE / 'results').mkdir(exist_ok=True)
    out = {'arch': args.arch, 'overrides': ov, 'tests': {}}
    print(f'{"test":16s} {"measured":>9s} {"uiCA":>8s} {"error":>7s}   {"ld fb_hit/iter":>15s} {"ld l1_miss/iter":>16s} '
          f'{"fb_full frac":>13s}   (each: model | hardware)')
    errs = []
    for t in args.tests:
        r = res[t]
        m = gt.get(t, {}).get('cycles_per_iter')
        if 'error' in r:
            print(f'{t:16s} ERROR {r["error"]}')
            continue
        e = r['TP'] / m - 1 if m else None
        if e is not None:
            errs.append(abs(e))
        pi, hw = r['per_iter'], gt.get(t, {}).get('per_iter', {})
        mFb = pi.get('ld_FB', 0.0)
        mMiss = sum(v for k, v in pi.items() if k.startswith('ld_') and k not in ('ld_L1', 'ld_FB', 'ld_SB'))
        mFull = r['avg'].get('fb_full_frac', 0.0)
        hFull = hw.get('l1d_pend_miss.fb_full', 0) / hw['cycles'] if hw.get('cycles') else float('nan')
        print(f'{t:16s} {m if m else float("nan"):9.2f} {r["TP"]:8.2f} {e if e is not None else float("nan"):+7.1%}   '
              f'{mFb:6.2f} | {hw.get("mem_load_retired.fb_hit", float("nan")):5.2f}  '
              f'{mMiss:6.2f} | {hw.get("mem_load_retired.l1_miss", float("nan")):5.2f}   '
              f'{mFull:5.2f} | {hFull:5.2f}')
        out['tests'][t] = {'measured': m, 'uica': r['TP'], 'error': e, 'model': r}
    if errs:
        print(f'mean |error| {sum(errs) / len(errs):.1%} over {len(errs)} tests')
    (HERE / 'results' / f'{tag}.json').write_text(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
