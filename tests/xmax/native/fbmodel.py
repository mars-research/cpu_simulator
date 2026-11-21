"""Cycle model of the fill-buffer (FB) rules for the bw loop: per iteration i, prefetch line i+D, demand-load line i.

Structures (per core, one thread):
  FB      F slots.
  uncore  at most C requests outstanding between L2 and memory; each takes W cycles to come back to L2.
  ROB     at most R iterations between the oldest unretired one and the newest dispatched one.
  LB      load buffer, LB entries. Every load and every prefetch takes one when it is allocated (dispatch here;
          allocation stalls if fewer than 2 are free). A demand load releases it at retirement. A prefetch releases
          it by rule pfrel: 'retire' (in order, like a load), 'complete' (when the uop completes), 'data' (when its
          line arrives at its fill level: L1 for t0, L2 for t1).
  issue   at most one iteration per c0 cycles (front-end / execution limit when nothing stalls).

Rules at prefetch dispatch (needs an uncore slot; if none, dispatch stalls):
  FB available -> take FB ("swpf" path). t0 holds it until the line is in L1 (W + r), t1 until it is in L2 (W).
      prio='wait': available = free and nobody waiting (waiters are served first, oldest first).
      prio='new' : available = free; a newly dispatched request takes a free FB before the waiters.
  otherwise    -> request goes to L2 without an FB ("hwpf" path), uncore slot for W cycles, and
      t0: the uop waits (in its LB entry) for an FB; when granted it completes and holds the FB until the line
          is in L1: max(grant, back at L2) + r.
      t1: the uop also waits (in its LB/ROB entry) for an FB; when granted it completes and holds the FB t1late
          cycles (its request is already out and the line only goes to L2). [old rule, t1wait=0: completes at once]
  A t1 that gets an FB at dispatch holds it t1hold cycles; t1hold < 0 means until its line is back in L2 (W).
  Q limits the t0 uops waiting with their request already sent. A t0 that finds no FB and no free Q slot
  waits without sending ("pfn"); when granted an FB its request leaves then, and it holds the FB W + r.
Demand load of line i:
  t0: completes when the line is in L1 (L1 hit / FB hit).
  t1: the line is in L2; the load waits for an FB (same rule as above) and holds it max(grant, back at L2) + r.
Retirement is in order: an iteration retires when its prefetch uop and its demand load have completed.
"""
import sys, heapq, collections

def run(kind, Q=10**9, F=16, W=390, r=16, C=29, R=36, LB=192, pfrel='retire', prio='wait', t1wait=1, t1hold=-1, t1late=0,
        c0=3.5, D=64, cycles=400_000, warm=100_000, pfonly=0, trace=None, G=99):
    # G = FB grants to waiting requests per cycle
    # trace=(t_from, t_to): print every event in that cycle range with the FB contents and the wait queue
    fb = []                      # heap of FB release times
    unc = []                     # heap of uncore completion times
    lbq = []                     # heap of scheduled LB releases
    lb = 0                       # LB entries in use
    waitq = collections.deque()  # FIFO of [iter, 'pf'|'pfn'|'ld', back_at_L2]
    l2_at, l1_at = {}, {}        # line -> time it is back in L2 / in L1
    pf_done, ld_done = {}, {}    # iteration -> completion time (None = not yet)
    oldest, nxt, next_issue = 0, 0, 0.0
    st = collections.Counter(); occ = collections.Counter()

    log = []
    def ev(t, msg):
        if trace and trace[0] <= t <= trace[1]: log.append(msg)

    def pf_lb_release(i, t_complete, t_data):          # schedule the prefetch's LB release, if its rule says so
        if pfrel == 'complete': heapq.heappush(lbq, t_complete)
        elif pfrel == 'data': heapq.heappush(lbq, t_data)

    def grant(t):                                       # free FBs go to the waiters, oldest first
        g = 0
        while waitq and len(fb) < F and g < G:
            g += 1
            i, what, back = waitq.popleft()
            if what == 'pfn':                           # request leaves only now, with its FB
                back = t + W; heapq.heappush(unc, back)
            if what == 'pf1':                           # t1 granted late: short hold, line goes to L2 only
                heapq.heappush(fb, (t + t1late, f'#{i}*'))
                if t >= warm: st['ho'] += t1late; st['no'] += 1; st['k'] += t - (back - W)
                pf_done[i] = t; pf_lb_release(i, t, back)
                ev(t, f'grant #{i} (t1, waited {t - back + W}) -> holds until {t + t1late}, uop completes')
                continue
            rel = max(t, back) + r
            if what in ('pf', 'pfn'):
                heapq.heappush(fb, (rel, f'#{i}*'))
                if t >= warm: st['ho'] += rel - t; st['no'] += 1; st['k'] += t - (back - W)
                pf_done[i] = t; l1_at[i + D] = rel; pf_lb_release(i, t, rel)
                ev(t, f'grant #{i} (t0, waited {t - back + W}) -> holds until {rel} (line in L2 at {back}, + r), uop completes')
            else:
                heapq.heappush(fb, (rel, f'L{i}'))
                ld_done[i] = rel

    def fb_available():
        return len(fb) < F and (prio == 'new' or not waitq)

    for t in range(cycles):
        while fb and fb[0][0] <= t:
            _, lab = heapq.heappop(fb); ev(t, f'release {lab}')
        while unc and unc[0] <= t: heapq.heappop(unc)
        while lbq and lbq[0] <= t: heapq.heappop(lbq); lb -= 1
        if prio == 'wait': grant(t)
        # dispatch at most one iteration
        if t >= next_issue and nxt - oldest < R and len(unc) < C and lb + 2 <= LB:
            i = nxt; line = i + D; back = t + W; lb += 2
            heapq.heappush(unc, back); l2_at[line] = back
            hinted = not fb_available()
            if not hinted:
                hold_to = back + r if kind == 't0' else (back if t1hold < 0 else t + t1hold)
                heapq.heappush(fb, (hold_to, f'#{i}'))
                if t >= warm: st['hd'] += hold_to - t; st['nd'] += 1
                ev(t, f'dispatch #{i}: FB free -> takes it, holds until {hold_to}, uop completes now')
                pf_done[i] = t
                if kind == 't0': l1_at[line] = back + r
                pf_lb_release(i, t, back + r if kind == 't0' else back)
            elif kind == 't0':
                if sum(1 for w in waitq if w[1] == 'pf') < Q:
                    waitq.append([i, 'pf', back]); pf_done[i] = None
                    ev(t, f'dispatch #{i}: FB full -> request sent to L2 (back at {back}), uop waits for an FB')
                else:                                   # no Q slot: no request sent yet
                    unc.remove(back); heapq.heapify(unc); l2_at.pop(line)
                    waitq.append([i, 'pfn', 0]); pf_done[i] = None; hinted = False
            elif t1wait:
                waitq.append([i, 'pf1', back]); pf_done[i] = None
                ev(t, f'dispatch #{i}: FB full -> request sent to L2 (back at {back}), uop waits for an FB')
            else:
                pf_done[i] = t; pf_lb_release(i, t, back)
            if t >= warm: st['pf'] += 1; st['hint'] += hinted
            # demand load of line i (prefetched D iterations earlier; the first D lines count as warm)
            if pfonly: ld_done[i] = t
            elif kind == 't0':
                if i in l1_at: ld_done[i] = max(t, l1_at[i])
                else: ld_done[i] = None if i >= D else t      # None: its t0 is still waiting for an FB
            elif i >= D:
                if fb_available():
                    rel = max(t, l2_at[i]) + r; heapq.heappush(fb, (rel, f'L{i}')); ld_done[i] = rel
                else:
                    waitq.append([i, 'ld', l2_at[i]]); ld_done[i] = None
            else:
                ld_done[i] = t
            nxt += 1; next_issue = t + c0
        if prio == 'new': grant(t)
        # t0 loads whose line arrived in L1 after the load dispatched
        if kind == 't0':
            for i in range(oldest, nxt):
                if ld_done.get(i) is None and i in l1_at: ld_done[i] = max(t, l1_at[i])
        # in-order retirement; the demand load (and a 'retire' prefetch) frees its LB entry here
        while oldest < nxt and pf_done.get(oldest) is not None and ld_done.get(oldest) is not None \
                and pf_done[oldest] <= t and ld_done[oldest] <= t:
            pf_done.pop(oldest); ld_done.pop(oldest); l2_at.pop(oldest, None); l1_at.pop(oldest, None)
            lb -= 2 if pfrel == 'retire' else 1
            ev(t, f'retire #{oldest}'); oldest += 1; st['ret'] += t >= warm
        if log:
            slots = ' '.join(f'[{lab}..{rel}]' for rel, lab in sorted(fb)) + ' [ ]' * (F - len(fb))
            wq = ' '.join(f'#{w[0]}' for w in waitq if w[1] != 'ld') or '-'
            print(f't={t:5d}  ' + ' | '.join(log)); print(f'         FB: {slots}   waiting: {wq}   ROB #{oldest}..#{nxt - 1}')
            log.clear()
        if t >= warm:
            occ['unc'] += len(unc); occ['fb'] += len(fb); occ['lb'] += lb; occ['rob'] += nxt - oldest
            occ['wait'] += sum(1 for w in waitq if w[1] in ('pf', 'pf1'))
    T = cycles - warm
    return dict(kind=kind, cyc_per_line=T / st['ret'], p_hint=st['hint'] / st['pf'], U=occ['unc'] / T,
                FB_busy=occ['fb'] / T, LB=occ['lb'] / T, ROB_iters=occ['rob'] / T, pf_waiting=occ['wait'] / T,
                H_d=st['hd'] / max(st['nd'], 1), H_o=st['ho'] / max(st['no'], 1), k=st['k'] / max(st['no'], 1),
                H=(st['hd'] + st['ho']) / max(st['nd'] + st['no'], 1))

def fmt(m):
    return (f"{m['kind']}: cycles/line {m['cyc_per_line']:.1f}  p_hint {m['p_hint']:.3f}  U {m['U']:.1f}  "
            f"FB {m['FB_busy']:.1f}  LB {m['LB']:.1f}  ROB iters {m['ROB_iters']:.1f}  pf waiting {m['pf_waiting']:.1f}  "
            f"H_d {m['H_d']:.0f}  H_o {m['H_o']:.0f}  k {m['k']:.0f}  H {m['H']:.0f}")

if __name__ == '__main__':
    kw = {}
    for a in sys.argv[1:]:
        k, v = a.split('=')
        kw[k] = v if k in ('pfrel', 'prio') else (int(v) if k in ('Q', 'F', 'C', 'R', 'D', 'r', 'LB', 'cycles', 'warm', 't1wait', 't1hold', 't1late', 'pfonly', 'G') else float(v))
    for kind in ('t0', 't1'):
        print(fmt(run(kind, **kw)))
