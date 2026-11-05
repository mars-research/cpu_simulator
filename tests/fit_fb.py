#!/usr/bin/env python3
"""Fits the FB-station service times (memsys fbServiceTime per request type) and the L2Q-station size
(nL2Q) to the measured throughput-bound DRAM tests, by coordinate descent on the mean |error|.

    ../.venv/bin/python fit_fb.py [--rounds 3] [--start ld=244 t0=150 t2=120 nL2Q=48]

FIT tests decide the parameters; VALIDATE tests are only reported (latency-bound chases, where the
FB station must not matter). Writes results/fit_fb.json (trajectory + best) and prints the table.
"""
import argparse, json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import run_uica

HERE = Path(__file__).resolve().parent
FIT = ['rand_dram', 'pf_t0_dram', 'pf_t2_dram', 'mix_t2x2_dram', 'mix_ld_t2x1_dram', 'mix_ld_t2x2_dram',
       'mix_t0_t2x1_dram', 'mix_ld_t0x1_dram', 'mchase24_dram', 'mchase32_dram', 'mchase48_dram']
VALIDATE = ['chase_l2', 'chase_llc', 'chase_dram', 'mchase8_dram', 'mchase16_dram', 'mchase20_dram',
            'mchase40_dram']
GT = json.loads((HERE / 'ground_truth.json').read_text())
CACHE = {}


def overrides(p):
    return {'fbServiceTime': {'ld': p['ld'], 't0': p['t0'], 't2': p['t2']}, 'nL2Q': p['nL2Q']}


def evaluate(p, tests):
    key = (tuple(sorted(p.items())), tuple(tests))
    if key not in CACHE:
        with ThreadPoolExecutor(8) as ex:
            res = dict(ex.map(lambda t: run_uica.run(t, 'EMR', overrides(p)), tests))
        CACHE[key] = {t: (res[t]['TP'], res[t]['TP'] / GT[t]['cycles_per_iter'] - 1) for t in tests}
    r = CACHE[key]
    return sum(abs(e) for _, e in r.values()) / len(r), r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rounds', type=int, default=3)
    ap.add_argument('--start', nargs='*', default=['ld=244', 't0=150', 't2=120', 'nL2Q=48'])
    args = ap.parse_args()
    p = {k: int(v) for k, v in (a.split('=') for a in args.start)}
    best, _ = evaluate(p, FIT)
    traj = [{'params': dict(p), 'err': best}]
    print(f'start {p} -> {best:.1%}', flush=True)
    for rnd in range(args.rounds):
        step = [0.25, 0.10, 0.04][min(rnd, 2)]
        for k in ['t2', 't0', 'ld', 'nL2Q']:
            improved = True
            while improved:
                improved = False
                for f in (1 - step, 1 + step):
                    q = dict(p, **{k: max(1, round(p[k] * f))})
                    e, _ = evaluate(q, FIT)
                    if e < best - 1e-4:
                        p, best, improved = q, e, True
                        traj.append({'params': dict(p), 'err': best})
                        print(f'  round {rnd} {k}: {p} -> {best:.1%}', flush=True)
                        break
    _, fitRes = evaluate(p, FIT)
    valErr, valRes = evaluate(p, VALIDATE)
    out = {'best': p, 'fit_mean_abs_err': best, 'validate_mean_abs_err': valErr, 'trajectory': traj,
           'tests': {t: {'measured': GT[t]['cycles_per_iter'], 'uica': tp, 'error': e}
                     for t, (tp, e) in {**fitRes, **valRes}.items()}}
    (HERE / 'results').mkdir(exist_ok=True)
    (HERE / 'results' / 'fit_fb.json').write_text(json.dumps(out, indent=1))
    print(f'\nbest {p}: fit {best:.1%}, validate {valErr:.1%}')
    for t, v in out['tests'].items():
        print(f"  {'fit' if t in FIT else 'val':3s} {t:18s} {v['measured']:8.2f} {v['uica']:8.2f} {v['error']:+7.1%}")


if __name__ == '__main__':
    main()
