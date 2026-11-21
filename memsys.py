"""Memory-system model for uiCA (execution-driven mode, -state): L1 fill buffers (FB), L2, the L2's queue to the
uncore (XQ) and memory, advanced cycle by cycle next to the core model.

Every load uop (software prefetches are load uops without an output) that the scheduler dispatches calls
access(); the returned Req is resolved later, once the model knows when the uop finishes (prefetch) or when its
data is ready (load). Until then the core keeps the uop in flight (ROB, load buffer). uiCA calls tick(clock) at
the start and endCycle(clock) at the end of every cycle.

Request path of an L1 miss (times in core cycles):

    dispatch t -- FB free? --yes--> takes an FB ("direct"; cf. l2_rqsts.swpf_miss / a demand miss)
         |                          the request reaches the L2 at t + l2Pipe
         no
         +--> fewer than nOverflow overflowed requests out? --yes--> the request goes out without an FB
         |    ("overflow"; sent as an L2 hardware prefetch, cf. l2_rqsts.hwpf_miss) and reaches the L2 at
         |    t + l2Pipe; the uop waits in the core for an FB. An overflowed request counts as out until its
         |    data is back in L2.
         +--no--> "wait": the uop waits for an FB without sending anything; once granted, it continues as a
              direct request from the grant (its request leaves then; cf. l2_rqsts.swpf_miss)
    at the L2: hit -> data in L2 at once; miss -> needs an XQ entry (nXQ), waits in order while all are taken
              (an overflowed prefetcht1/t2 that finds the XQ full is dropped instead, if dropT2OnXQFull);
              the entry is held until the data is back in L2: llcLat (LLC hit) or memLat (memory) later
    data in L1 = max(data in L2, FB grant) + l2ToL1   (ld, t0 only; prefetcht1/t2 fill only the L2)

FB hold (release time):
    direct    ld/t0: until the data is in L1.   t1/t2: until the data is in L2 (t2DirectHold = 'l2') or a fixed
              number of cycles after the grant.
    overflow  ld/t0: until the data is in L1 (>= grant + l2ToL1).   t1/t2: t2OverflowHold cycles after the grant.
FB arbitration (fbArb): 'old' = waiting requests take free FBs first (oldest first), at the start of a cycle;
    'new' = requests dispatched in a cycle take free FBs first, waiting ones get what is left at its end.
Uop completion: a prefetch completes 1 cycle after it has its FB (direct: right at dispatch); a load when its
    data is in L1. An access to a line already in flight merges with it (level FB).

Statistics (summary): per kind (ld, pft0, pft1, ...): p = overflow share of its misses, H_d / H_o / H_w = FB hold of
direct / overflowed / waited misses, H = mean hold, k / k_w = time an overflowed / waited uop waits for its FB; per cycle: fb_full (all
FBs taken), fb_wait (some uop waiting for an FB; cf. l1d_pend_miss.fb_full), xq_full (all XQ entries taken; cf.
xq.full_cycles), U = mean L2 misses outstanding from L2 arrival until the data is back (cf.
offcore_requests_outstanding.data_rd), W = their mean time.
Caches are set-associative LRU; the LLC is non-inclusive (L2 victims go to the LLC). Stores are not modelled.
"""
import heapq
from collections import OrderedDict, deque

LINE = 64

PRESETS = {
   # Xeon Max 9462 (Sapphire Rapids + HBM), one thread on one core, data on HBM node 2, core 2.69 GHz.
   # Golden Cove: L1D 48 KiB 12-way, 16 FBs; L2 2 MiB 16-way; LLC 112.5 MiB per socket.
   # memLat: measured W (offcore_requests_outstanding.data_rd / offcore_requests.data_rd) at 1 core, ~121 ns.
   'XMAX_hbm': {
      'L1Size': 48 << 10, 'L1Assoc': 12, 'L1Lat': 5,
      'L2Size': 2 << 20, 'L2Assoc': 16,
      'LLCSize': 112 << 20, 'LLCAssoc': 15, 'llcLat': 73,
      'memLat': 325,
      'l2Pipe': 11,            # L1 miss -> request at the L2
      'l2ToL1': 11,            # data in L2 -> data in L1
      'nFB': 16,
      'nXQ': 36,               # L2 misses outstanding per core (prefetch-only t1 saturates at ~35, xq.full 92 %)
      'fbArb': 'old',
      'nOverflow': 10**9,      # overflowed requests out at once (sent, data not yet back in L2)
      't2DirectHold': 'l2',
      't2OverflowHold': 1,
      'dropT2OnXQFull': True,
   },
}


class Cache:
   """Set-associative LRU over line numbers (state only, no timing)."""
   def __init__(self, size, assoc):
      self.assoc = assoc
      self.nSets = max(1, size // (assoc * LINE))
      self.sets = {}

   def lookup(self, line):
      s = self.sets.get(line % self.nSets)
      if s is not None and line in s:
         s.move_to_end(line)
         return True
      return False

   def insert(self, line):
      """-> evicted line or None"""
      s = self.sets.setdefault(line % self.nSets, OrderedDict())
      if line in s:
         s.move_to_end(line)
         return None
      s[line] = True
      if len(s) > self.assoc:
         return s.popitem(last=False)[0]
      return None


class Req:
   """One access. Once resolved, ready (data ready; loads) and done (earliest finish of the uop) are known."""
   __slots__ = ('rnd', 'kind', 'toL1', 'line', 't', 'path', 'level', 'fbGrant', 'fbRelease', 'atL2', 'l2Data',
                'l1Data', 'ready', 'done', 'resolved', 'merged')

   def __init__(self, rnd, kind, toL1, line, t):
      self.rnd, self.kind, self.toL1, self.line, self.t = rnd, kind, toL1, line, t
      self.path = None        # 'direct' | 'overflow' (L1 misses only)
      self.level = None       # L1, FB (merged), L2, LLC, MEM (+ '*': sent without an FB), drop
      self.fbGrant = self.fbRelease = self.atL2 = self.l2Data = self.l1Data = None
      self.ready = self.done = None
      self.resolved = False
      self.merged = []        # loads that hit this line while it is on its way to L1


class MemSystem:
   def __init__(self, config, regions, bases):
      """config: preset name, or {"preset": name, ...overrides...}; regions: {name: {"size", "warm"}};
      bases: {name: base address}"""
      cfg = config or 'XMAX_hbm'
      if isinstance(cfg, str):
         cfg = {'preset': cfg}
      self.cfg = dict(PRESETS[cfg.get('preset', 'XMAX_hbm')])
      self.cfg.update({k: v for k, v in cfg.items() if k != 'preset'})
      c = self.cfg
      self.L1, self.L2, self.LLC = Cache(c['L1Size'], c['L1Assoc']), Cache(c['L2Size'], c['L2Assoc']), Cache(c['LLCSize'], c['LLCAssoc'])
      self.fb = []                 # requests holding an FB
      self.fbWait = deque()        # overflowed requests waiting for an FB, oldest first
      self.toL2 = []               # heap (atL2, seq, req): requests on their way to the L2
      self.xq = []                 # heap (l2Data, seq, req): XQ entries in use
      self.xqWait = deque()        # requests at the L2 waiting for an XQ entry
      self.inflight = {}           # line -> request bringing it in
      self.seq = 0
      self.outstanding = 0         # L2 misses from L2 arrival until the data is back
      self.nOvf = 0                # overflowed requests out (sent without an FB, data not yet back in L2)
      self.ovfRel = []             # heap: when they stop counting
      self.reqs = []               # L1-miss requests (statistics)
      self.events = []             # (rnd, kind, level, t); uiCA reads [2] as the uop's memory level
      self.samples = {}            # clock -> (FBs taken, uops waiting for an FB, XQ entries taken, outstanding)
      for name, r in regions.items():
         if r.get('warm'):
            self.warm(bases[name], int(r['size']))

   # --- cache state ---------------------------------------------------------------------------------
   def _fillL2(self, line):
      victim = self.L2.insert(line)
      if victim is not None:
         self.LLC.insert(victim)

   def warm(self, base, size):
      """Touch every line of a region once (in address order), as an untimed warm-up lap would."""
      nLines = size // LINE
      if nLines > 4 * (self.cfg['LLCSize'] // LINE):
         return
      first = base // LINE
      for line in range(first, first + nLines):
         self._fillL2(line)
         self.L1.insert(line)

   # --- interface used by uiCA ----------------------------------------------------------------------
   def covers(self, instrI):
      return bool(getattr(instrI, 'memAddrs', None))

   def addressOf(self, instrI):
      return instrI.memAddrs[0]

   def canIssue(self, instrI, t):
      return True                  # a request that finds the FBs full waits in the core, not in the scheduler

   def noteBlocked(self, t):
      pass

   def noteForwarded(self, rnd, t):
      self.events.append((rnd, 'ld', 'SB', t))

   def access(self, instrI, t, isPrefetch, prefetchHint, firstTry=None):
      c = self.cfg
      line = self.addressOf(instrI) // LINE
      toL1 = (not isPrefetch) or prefetchHint in ('t0', 'nta')
      kind = 'ld' if not isPrefetch else 'pf' + prefetchHint
      r = Req(instrI.rnd, kind, toL1, line, t)
      prev = self.inflight.get(line)
      if prev is not None and prev.level != 'drop' and (prev.toL1 or not toL1):
         r.level = 'FB'                                    # merges with the miss in flight
         if isPrefetch:
            self._resolve(r, None, t + 1)
         elif prev.l1Data is not None:
            d = max(t + c['L1Lat'], prev.l1Data)
            self._resolve(r, d, d)
         else:
            prev.merged.append(r)
      elif toL1 and self.L1.lookup(line):
         r.level = 'L1'
         self._resolve(r, None if isPrefetch else t + c['L1Lat'], t + 1)
      elif not toL1 and (self.L1.lookup(line) or self.L2.lookup(line)):
         r.level = 'L2'                                    # prefetcht1/t2 of a cached line
         self._resolve(r, None, t + 1)
      else:
         self._miss(r, t)
      self.events.append((r.rnd, kind, r.level, t))
      return r

   def _miss(self, r, t):
      inL2 = self.L2.lookup(r.line)
      r.level = 'L2' if inL2 else ('LLC' if self.LLC.lookup(r.line) else 'MEM')
      self.reqs.append(r)
      self.inflight[r.line] = r
      if len(self.fb) < self.cfg['nFB'] and not (self.cfg['fbArb'] == 'old' and self.fbWait):
         r.path = 'direct'
         self._grant(r, t)
      elif self.nOvf < self.cfg['nOverflow']:
         r.path = 'overflow'
         self.nOvf += 1
         self.fbWait.append(r)
         if not inL2:
            r.level += '*'
      else:
         r.path = 'wait'                                   # nothing is sent before the FB grant
         self.fbWait.append(r)
         return
      self._send(r, t)

   def _send(self, r, t):
      r.atL2 = t + self.cfg['l2Pipe']
      self.seq += 1
      heapq.heappush(self.toL2, (r.atL2, self.seq, r))

   def _grant(self, r, g):
      c = self.cfg
      r.fbGrant = g
      self.fb.append(r)
      if r.path == 'wait':
         self._send(r, g)
      if not r.toL1:
         if r.path == 'overflow':
            r.fbRelease = g + c['t2OverflowHold']
         elif c['t2DirectHold'] != 'l2':
            r.fbRelease = g + c['t2DirectHold']
      self._update(r)

   def _update(self, r):
      """fill in the times that are known now (called on every event of r); resolve r when they are"""
      c = self.cfg
      if r.toL1 and r.l1Data is None and r.l2Data is not None and r.fbGrant is not None:
         r.l1Data = max(r.l2Data, r.fbGrant) + c['l2ToL1']
         r.fbRelease = r.l1Data
         self.L1.insert(r.line)
         for m in r.merged:
            d = max(m.t + c['L1Lat'], r.l1Data)
            self._resolve(m, d, d)
         r.merged = []
      if not r.toL1 and r.fbRelease is None and r.fbGrant is not None and r.l2Data is not None:
         r.fbRelease = max(r.l2Data, r.fbGrant + 1)
      if not r.resolved:
         if r.kind == 'ld':
            if r.l1Data is not None:
               self._resolve(r, r.l1Data, r.l1Data)
         elif r.fbGrant is not None:
            self._resolve(r, None, r.fbGrant + 1)

   def _resolve(self, r, ready, done):
      r.ready, r.done, r.resolved = ready, done, True

   # --- per-cycle update ------------------------------------------------------------------------------
   def tick(self, clock):
      c = self.cfg
      self.fb = [r for r in self.fb if r.fbRelease is None or r.fbRelease > clock]     # FB holds that ended
      while self.ovfRel and self.ovfRel[0] <= clock:                                     # overflowed: data in L2
         heapq.heappop(self.ovfRel)
         self.nOvf -= 1
      while self.xq and self.xq[0][0] <= clock:                                          # data back in L2
         heapq.heappop(self.xq)
         self.outstanding -= 1
      while self.toL2 and self.toL2[0][0] <= clock:                                      # requests at the L2
         _, _, r = heapq.heappop(self.toL2)
         if r.level == 'L2':
            r.l2Data = clock
            self._ovfOut(r)
            self._update(r)
         elif not r.toL1 and r.path == 'overflow' and c['dropT2OnXQFull'] and len(self.xq) >= c['nXQ']:
            r.level = 'drop'
            r.l2Data = clock
            self._ovfOut(r)
            self.inflight.pop(r.line, None)
         else:
            self.outstanding += 1
            self.xqWait.append(r)
      while self.xqWait and len(self.xq) < c['nXQ']:                                     # XQ departures, in order
         r = self.xqWait.popleft()
         r.l2Data = clock + (c['llcLat'] if r.level.startswith('LLC') else c['memLat'])
         self.seq += 1
         heapq.heappush(self.xq, (r.l2Data, self.seq, r))
         self._fillL2(r.line)
         self._ovfOut(r)
         self._update(r)
      if c['fbArb'] == 'old':
         self._grantWaiters(clock)

   def _ovfOut(self, r):
      if r.path == 'overflow':
         heapq.heappush(self.ovfRel, r.l2Data)

   def endCycle(self, clock):
      if self.cfg['fbArb'] == 'new':
         self._grantWaiters(clock)
      for line in [l for l, r in self.inflight.items()
                   if (r.l1Data if r.toL1 else r.l2Data) is not None and (r.l1Data if r.toL1 else r.l2Data) <= clock]:
         del self.inflight[line]
      self.samples[clock] = (len(self.fb), len(self.fbWait), len(self.xq), self.outstanding)

   def _grantWaiters(self, clock):
      while self.fbWait and len(self.fb) < self.cfg['nFB']:
         self._grant(self.fbWait.popleft(), clock)

   # --- reporting -----------------------------------------------------------------------------------
   def summary(self, firstRnd, lastRnd):
      nIter = max(1, lastRnd - firstRnd + 1)
      out = {'iterations': nIter, 'per_iter': {}, 'avg': {}}
      ev = [e for e in self.events if firstRnd <= e[0] <= lastRnd]
      if not ev:
         return out
      t0, t1 = min(e[-1] for e in ev), max(e[-1] for e in ev)
      s = [v for k, v in self.samples.items() if t0 <= k <= t1]
      n = max(1, len(s))
      out['window_cycles'] = (t0, t1)
      a = out['avg']
      a['fb_full_frac'] = sum(1 for x in s if x[0] >= self.cfg['nFB']) / n
      a['fb_wait_frac'] = sum(1 for x in s if x[1] > 0) / n
      a['fb_mean'] = sum(x[0] for x in s) / n
      a['xq_full_frac'] = sum(1 for x in s if x[2] >= self.cfg['nXQ']) / n
      a['U'] = sum(x[3] for x in s) / n
      miss = [r for r in self.reqs if firstRnd <= r.rnd <= lastRnd and r.l2Data is not None
              and not r.level.startswith('L2') and r.level != 'drop']
      a['W'] = sum(r.l2Data - r.atL2 for r in miss) / max(1, len(miss))
      for k in sorted({e[1] for e in ev}):
         for lvl in sorted({e[2] for e in ev if e[1] == k}):
            out['per_iter'][f'{k}_{lvl}'] = sum(1 for e in ev if e[1] == k and e[2] == lvl) / nIter
         rk = [r for r in self.reqs if r.kind == k and firstRnd <= r.rnd <= lastRnd and r.fbRelease is not None]
         if rk:
            d, o = [r for r in rk if r.path == 'direct'], [r for r in rk if r.path == 'overflow']
            hold = lambda r: r.fbRelease - r.fbGrant
            a[f'{k}_p'] = len(o) / len(rk)
            w = [r for r in rk if r.path == 'wait']
            a[f'{k}_wait_share'] = len(w) / len(rk)            # waited for an FB before sending (counted as swpf)
            a[f'{k}_H_w'] = sum(map(hold, w)) / max(1, len(w))
            a[f'{k}_k_w'] = sum(r.fbGrant - r.t for r in w) / max(1, len(w))
            a[f'{k}_H'] = sum(map(hold, rk)) / len(rk)
            a[f'{k}_H_d'] = sum(map(hold, d)) / max(1, len(d))
            a[f'{k}_H_o'] = sum(map(hold, o)) / max(1, len(o))
            a[f'{k}_k'] = sum(r.fbGrant - r.t for r in o) / max(1, len(o))
      return out


def prefetchHintOf(instr):
   s = instr.instrStr.upper()
   for h in ('PREFETCHT0', 'PREFETCHT1', 'PREFETCHT2', 'PREFETCHNTA', 'PREFETCHW'):
      if s.startswith(h):
         return {'PREFETCHT0': 't0', 'PREFETCHT1': 't1', 'PREFETCHT2': 't2', 'PREFETCHNTA': 'nta', 'PREFETCHW': 't0'}[h]
   return None
