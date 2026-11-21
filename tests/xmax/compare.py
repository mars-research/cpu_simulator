"""Simulates every case in ground_truth.json with uiCA and prints model vs measured.
usage: ../../.venv/bin/python compare.py [--set key=value ...] [--lines N] [--only core|mem]
  --set: memsys config overrides (memsys.PRESETS['XMAX_hbm'] keys), e.g. --set nOverflow=12 fbArb=new
Kernels/states are generated with gen.sh into build/; results also go to build/compare.json."""
import json, os, subprocess, sys, argparse
from concurrent.futures import ThreadPoolExecutor
H = os.path.dirname(os.path.abspath(__file__)); U = os.path.normpath(os.path.join(H, '../..'))
ap = argparse.ArgumentParser(); ap.add_argument('--set', nargs='*', default=[]); ap.add_argument('--lines', type=int, default=3000)
ap.add_argument('--only', choices=['core', 'mem']); a = ap.parse_args()
gt = json.load(open(f'{H}/ground_truth.json'))
cfg = ', '.join(f'"{k}": {v if v.replace(".", "", 1).lstrip("-").isdigit() or v in ("true", "false") else json.dumps(v)}'
                for k, v in (s.split('=') for s in a.set))

def sim(kind, pad, off):
    tag = f'cmp_{kind}'; env = dict(os.environ, TAG=tag, CFG=cfg)
    if kind == 'core':
        env.update(PFO_MASK='0xff', PFO_SIZE='16384', PFO_WARM='true')
    subprocess.run([f'{H}/gen.sh', 't0', str(pad), str(a.lines)], env=env, check=True, capture_output=True)
    k = f'pfo_t0_p{pad}_{tag}'
    subprocess.run([f'{U}/.venv/bin/python', f'{U}/uiCA.py', f'{H}/build/{k}.o', '-arch', 'EMR', '-alignmentOffset', str(off),
                    '-state', f'{H}/states/{k}.json', '-memJson', f'{H}/build/{k}.mem.json', '-TPonly'], check=True, capture_output=True, cwd=H)
    return json.load(open(f'{H}/build/{k}.mem.json'))

jobs = []
if a.only != 'mem':
    jobs += [('core', int(p), v['alignmentOffset']) for p, v in gt['core_only'].items()]
if a.only != 'core':
    off = {p: v['alignmentOffset'] for p, v in gt['core_only'].items()}
    jobs += [('mem', int(p), off[p]) for p in gt['pfo_t0_misses']]
with ThreadPoolExecutor(os.cpu_count()) as ex:
    res = dict(zip(jobs, ex.map(lambda j: sim(*j), jobs)))
out, errs = {}, []
if a.only != 'mem':
    print('core only (L1-resident), cycles/line: pad  measured  model  error')
    for (k, p, _), m in res.items():
        if k == 'core':
            g = gt['core_only'][str(p)]['cycles_per_line']; e = m['TP'] / g - 1; errs.append(abs(e))
            print(f'  {p:3d}  {g:6.2f}  {m["TP"]:6.2f}  {e:+.0%}'); out[f'core_{p}'] = m['TP']
if a.only != 'core':
    print('prefetch-only t0 with misses: pad | cycles/line | U | W | p | fb_full (model: fb_wait) | xq_full | model shares d/o/w, H_d H_o H_w, k k_w')
    for (k, p, _), m in res.items():
        if k == 'mem':
            g = gt['pfo_t0_misses'][str(p)]; s = m['avg']
            for x, y in ((m['TP'], g['cycles_per_line']), (s['U'], g['U'])):
                errs.append(abs(x / y - 1))
            xq = f"{g['xq_full_frac']:.2f}" if 'xq_full_frac' in g else '-'
            print(f"  {p:3d} | {g['cycles_per_line']:5.1f} vs {m['TP']:5.1f} | {g['U']:4.1f} vs {s['U']:4.1f} | {g['W']:4.0f} vs {s['W']:4.0f} | "
                  f"{g['p_hwpf']:.2f} vs {s.get('pft0_p', 0):.2f} | {g['fb_full_frac']:.2f} vs {s['fb_wait_frac']:.2f} | {xq} vs {s['xq_full_frac']:.2f} | "
                  f"{1 - s.get('pft0_p', 0) - s.get('pft0_wait_share', 0):.2f}/{s.get('pft0_p', 0):.2f}/{s.get('pft0_wait_share', 0):.2f}, "
                  f"{s.get('pft0_H_d', 0):.0f} {s.get('pft0_H_o', 0):.0f} {s.get('pft0_H_w', 0):.0f}, {s.get('pft0_k', 0):.0f} {s.get('pft0_k_w', 0):.0f}")
            out[f'mem_{p}'] = m
print(f'mean abs error (cycles/line, and U for the miss cases): {sum(errs) / len(errs):.1%}')
json.dump(out, open(f'{H}/build/compare.json', 'w'), indent=1)
