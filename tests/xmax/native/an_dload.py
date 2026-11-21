# usage: an_dload.py <tag>...  -- where the demand load of each iteration is served (14 instructions per iteration)
import sys, re
D = __import__('os').path.dirname(__import__('os').path.abspath(__file__)) + '/logs'
for tag in sys.argv[1:]:
    roi = float(re.search(r'ROI seconds\s*:\s*([\d.]+)', open(f'{D}/{tag}.out').read()).group(1))
    c = {l.split(',')[2]: float(l.split(',')[0]) for l in open(f'{D}/{tag}.core.perf') if l[0].isdigit()}
    rd = sum(float(l.split(',')[2]) for l in open(f'{D}/{tag}.unc.perf') if l.startswith('S0') and 'hbm_rd' in l)
    it = c['instructions'] / 14
    print(f"== {tag}: HBM {rd*32/roi/1e9:.1f} GB/s | HBM lines / iteration {rd*32/64/it:.3f}")
    s = 0
    for k in ['l1_hit', 'fb_hit', 'l1_miss', 'l2_hit']:
        v = c['mem_load_retired.' + k] / it; print(f"  mem_load_retired.{k:8s} / iteration = {v:.3f}")
        if k != 'l2_hit': s += v
    print(f"  l1_hit + fb_hit + l1_miss = {s:.3f}   | l1_miss not served by L2 (l1_miss - l2_hit) = {(c['mem_load_retired.l1_miss']-c['mem_load_retired.l2_hit'])/it:.3f}")
