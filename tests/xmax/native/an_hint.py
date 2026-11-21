# usage: an_hint.py <tag> <swpf counter name>   -- hint-path split for one run
import sys, re
D = __import__('os').path.dirname(__import__('os').path.abspath(__file__)) + '/logs'; tag, pfk = sys.argv[1], sys.argv[2]
roi = float(re.search(r'ROI seconds\s*:\s*([\d.]+)', open(f'{D}/{tag}.out').read()).group(1))
c = {l.split(',')[2]: float(l.split(',')[0]) for l in open(f'{D}/{tag}.core.perf') if l[0].isdigit()}
rd = sum(float(l.split(',')[2]) for l in open(f'{D}/{tag}.unc.perf') if l.startswith('S0') and 'hbm_rd' in l)
hbm_lines = rd * 32 / 64; pf = c[pfk]
print(f"== {tag}: ROI {roi:.2f} s | core {2.7*c['cycles']/c['ref-cycles']:.3f} GHz | IPC {c['instructions']/c['cycles']:.2f} | HBM read {rd*32/roi/1e9:.1f} GB/s")
for k in ['l2_rqsts.hwpf_miss', 'l2_rqsts.swpf_miss', 'l1d.replacement']:
    print(f"  {k:20s} / prefetch = {c[k]/pf:.3f}")
print(f"  HBM lines / prefetch = {hbm_lines/pf:.3f} | (hwpf+swpf) / prefetch = {(c['l2_rqsts.hwpf_miss']+c['l2_rqsts.swpf_miss'])/pf:.3f}")
