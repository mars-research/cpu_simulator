# usage: an_pebs.py <tag>...  -- distribution of prefetch dispatch -> completion latency (cycles) from pebs.sh
import sys, os
D = os.path.dirname(os.path.abspath(__file__)) + '/logs'
print(f"{'run':14s} {'samples':>8s} {'mean':>6s} {'p10':>5s} {'p50':>5s} {'p90':>5s} {'p99':>5s} {'<=8':>6s} {'>50':>6s} {'>200':>6s}")
for tag in sys.argv[1:]:
    w = sorted(int(x) for x in open(f'{D}/{tag}.lat') if x.strip().isdigit())
    n = len(w); q = lambda f: w[min(n - 1, int(f * n))]
    fr = lambda c: 100 * sum(c(x) for x in w) / n
    print(f"{tag:14s} {n:8d} {sum(w)/n:6.1f} {q(.1):5d} {q(.5):5d} {q(.9):5d} {q(.99):5d} {fr(lambda x: x <= 8):5.1f}% {fr(lambda x: x > 50):5.1f}% {fr(lambda x: x > 200):5.1f}%")
