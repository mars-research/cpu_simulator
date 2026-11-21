# usage: an_roi.py <tag> <ncores> [...]
import sys, re, collections
TSC = 2.7e9
def rd(fn):
    c = collections.Counter(); en = {}
    for l in open(fn):
        p = l.strip().split(',')
        if l.startswith('#') or len(p) < 5: continue
        if p[0] in ('S0', 'S1'):                       # --per-socket: S, ncpus, value, unit, event, enabled_ns, pct
            c[(p[0], p[4])] += float(p[2]); en[(p[0], p[4])] = float(p[5])
        else:
            c[p[2]] += float(p[0]); en[p[2]] = float(p[3])
    return c, en
args = sys.argv[1:]
for tag, n in zip(args[::2], map(int, args[1::2])):
    f = __import__('os').path.dirname(__import__('os').path.abspath(__file__)) + '/logs/' + tag
    roi = float(re.search(r'ROI seconds\s*:\s*([\d.]+)', open(f + '.out').read()).group(1))
    c, _ = rd(f + '.core.perf'); u, ue = rd(f + '.unc.perf')
    ghz = 2.7 * c['cycles'] / c['ref-cycles']
    Tc = c['cycles'] / (ghz * 1e9)                      # core-seconds = n x ROI
    busy = c['ref-cycles'] / (n * TSC * roi)
    rdB = 32 * u[('S0', 'hbm_rd')]; wrB = 32 * u[('S0', 'hbm_wr')]
    req = c['offcore_requests.data_rd']; occ = c['offcore_requests_outstanding.data_rd']
    print(f"== {tag}: ROI {roi:.2f} s, workers busy {100*busy:.1f} %")
    print(f"  core GHz {ghz:.3f} | mesh (CHA) GHz S0 {u[('S0','clk_cha')]/roi/1e9:.3f} | IPC {c['instructions']/c['cycles']:.2f}")
    print(f"  HBM socket0 read {rdB/roi/1e9:.1f} GB/s, write {wrB/roi/1e9:.2f} GB/s | socket1 read {32*u[('S1','hbm_rd')]/roi/1e9:.2f} GB/s")
    print(f"  HBM read lines/s per core {rdB/64/roi/n/1e6:.0f} M | L2 misses/s per core {req/Tc/1e6:.0f} M | HBM read bytes / (L2 misses x 64) {rdB/(req*64):.3f}")
    print(f"  in flight per core {occ/c['cycles']:.1f} | time per L2 miss {occ/req/ghz:.0f} ns | fb_full {100*c['l1d_pend_miss.fb_full']/c['cycles']:.0f} % | xq.full {100*c['xq.full_cycles']/c['cycles']:.0f} %")
