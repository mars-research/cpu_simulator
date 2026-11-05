"""Memory-system model for uiCA: non-constant load latency.

Stock uiCA gives every load a fixed latency (an L1 hit). In execution-driven mode (-state, see execstate.py)
every executed instruction instance carries the concrete addresses of its memory operands, and every load uop
(including software prefetches, which uiCA represents as load uops without an output register) asks this
model, at dispatch, when its data is ready:

    L1 hit                          t + L1 latency
    line already in flight to L1    max(t + L1, fill time)                     (fill-buffer hit / merge)
    L1 miss                         needs a fill buffer (FB) first; waits for one if all are busy
      L2 hit                        FB start + L2 latency
      L2 miss                       also needs an L2 miss-queue entry (L2Q); waits if all are busy
        LLC hit                     L2Q start + LLC latency
        DRAM                        L2Q start + DRAM latency (+ queueing behind the DRAM bandwidth)

Queueing view of the miss path: two multi-server stations in series.
    FB station   nFB servers. Every L1 miss (request type ld = demand load, t0 = prefetcht0/nta,
                 t2 = prefetcht1/t2) waits for a server, then occupies it for its service time
                 fbServiceTime[type] cycles, capped at the time until its data reaches its fill level
                 (L1 for ld/t0, L2 for t2). None = until the data arrives (the physical upper bound);
                 0 = the request passes through without occupying an FB. The per-type service times
                 are fitted (tests/fit_fb.py): the hardware holds an FB for less than the whole trip,
                 and for different times per type.
    L2Q station  nL2Q servers, entered when the FB is granted; service time = the LLC/DRAM trip.
Latencies are load-to-use latencies of a dependent load (what a pointer chase measures). A software
prefetch completes (frees its uop) as soon as it has its FB.

Fill-buffer busy bypass (fbBusyBypass, OFF by default, experimental; old name fbFullL2Hint): an L1 miss to LLC/DRAM that
finds every FB busy does not wait for one before going out. It enters the L2Q at once (the hardware sends
it as an L2 "hardware prefetch"), and then
    ld, t0   wait for an FB for the L2 -> L1 step and hold it until the data arrives; a prefetcht0
             completes only once it has its FB
    t2       needs no FB at all; it is dropped if the L2Q is full too
A miss that gets an FB at once holds it for the whole trip. With the bypass, uops are never kept back at
dispatch: requests waiting for an FB wait in the load buffer (bounded by the LSQ). Statistics mark
bypassed accesses LLC* / DRAM*, dropped prefetches 'drop'. Without the bypass, a load/prefetch that needs
an FB while all are busy stays in the scheduler and retries. Caches are set-associative LRU; the LLC is
non-inclusive (fills from DRAM go to L2/L1, L2 victims go to the LLC). Stores are not modelled.

Addresses come from the instance (InstrInstance.memAddrs, set by the execution-driven front end); the
regions of the state file (with their "warm" flags) and the parameters ("config") are passed in.
"""
import heapq, math
from collections import OrderedDict

LINE = 64

# Calibrated on CloudLab d760 (Xeon Gold 6548Y+, Emerald Rapids, 2.5 GHz core/uncore, prefetchers off)
# from tests/ground_truth.json: chase_l1 / chase_l2 / chase_llc / chase_dram load-to-use latencies.
# Sizes: Golden/Raptor Cove L1D 48 KiB 12-way, L2 2 MiB 16-way, LLC 60 MiB (1.875 MiB x 32 slices) 15-way.
PRESETS = {
   'EMR_d760': {
      'L1Size': 48 << 10, 'L1Assoc': 12, 'L1Lat': 5,
      'L2Size': 2 << 20, 'L2Assoc': 16, 'L2Lat': 16,
      'LLCSize': 60 << 20, 'LLCAssoc': 15, 'LLCLat': 73,
      'DRAMLat': 316,
      'nFB': 16,               # FB station servers: L1 fill buffers (documented Golden Cove value; tests/mchase*:
                               # demand L1-miss occupancy saturates at ~14, fb_full 85% of cycles)
      'fbBusyBypass': False,   # fill-buffer busy bypass (see module docstring). Off: with it, the best nBypassQ (28) gave
                               # 14.9 % mean error vs 6.7 % without (20 tests, 2026-11-05). Evidence for it
                               # (tests/results/counter_survey.md):
                               # with hardware prefetchers off, l2_rqsts.hwpf_miss is 57-85 % of L2 misses and tracks
                               # the FB-full fraction of cycles in every test; none where FBs are never full
      'fbServiceTime': {'ld': 244, 't0': 172, 't2': 122},   # FB service time per request type, cycles; None = held
                               # until the data arrives. Fitted without the bypass (tests/fit_fb.py)
      'nBypassQ': 28,          # bypassed requests outstanding (each until its data reaches L2); when full, a miss
                               # waits for an FB instead. Counter survey: 13-18 bypassed misses in flight in
                               # rand / pf_t0 / pf_t2 / mchase48 (l2_rqsts.hwpf_miss share x offcore occupancy)
      'nL2Q': 48,              # outstanding L2 misses per core (documented 48; counter survey: at most 44-47 in
                               # flight; the bypass runs used 44)
      'DRAMLinesPerCycle': 2.08,   # 8 ch DDR5-5200 = 332.8 GB/s at 2.5 GHz; one socket
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

   def contains(self, line):
      """like lookup, without updating the LRU order"""
      s = self.sets.get(line % self.nSets)
      return s is not None and line in s

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

   def remove(self, line):
      s = self.sets.get(line % self.nSets)
      if s is not None:
         s.pop(line, None)


class SlotResource:
   """N identical slots; acquire(t, hold) -> start time >= t of the earliest free slot."""
   def __init__(self, n, log=None):
      self.n = n
      self.busy = []   # heap of release times
      self.log = log   # optional list of (start, release) occupancy intervals

   def acquire(self, t, releaseFn):
      while self.busy and self.busy[0] <= t:
         heapq.heappop(self.busy)
      start = t
      if len(self.busy) >= self.n:
         start = max(t, heapq.heappop(self.busy))
      release = releaseFn(start)
      self.hold(release, start)
      return start, release

   def grant(self, t):
      """start time >= t of the earliest free server; the caller must then hold(release)"""
      while self.busy and self.busy[0] <= t:
         heapq.heappop(self.busy)
      if len(self.busy) >= self.n:
         return max(t, heapq.heappop(self.busy))
      return t

   def hold(self, release, start=None):
      heapq.heappush(self.busy, release)
      if self.log is not None and start is not None:
         self.log.append((start, release))

   def isFull(self, t):
      return sum(1 for r in self.busy if r > t) >= self.n

   def occupancy(self, t):
      return sum(1 for r in self.busy if r > t)


class MemSystem:
   def __init__(self, config, regions, bases):
      """config: preset name, or {"preset": name, ...overrides...}; regions: {name: {"size", "warm"}};
      bases: {name: base address}"""
      cfg = config or 'EMR_d760'
      if isinstance(cfg, str):
         cfg = {'preset': cfg}
      self.cfg = dict(PRESETS[cfg.get('preset', 'EMR_d760')])
      self.cfg.update({k: v for k, v in cfg.items() if k != 'preset'})
      c = self.cfg
      self.L1, self.L2, self.LLC = Cache(c['L1Size'], c['L1Assoc']), Cache(c['L2Size'], c['L2Assoc']), Cache(c['LLCSize'], c['LLCAssoc'])
      self.fbLog = []
      self.FB, self.L2Q = SlotResource(c['nFB'], self.fbLog), SlotResource(c['nL2Q'])
      self.bypass = bool(c.get('fbBusyBypass', c.get('fbFullL2Hint', False)))
      self.BQ = SlotResource(c.get('nBypassQ') or 10**9)
      self.dramNextFree = 0.0
      self.dramServiceTime = 1.0 / c['DRAMLinesPerCycle']
      self.inflightL1 = {}   # line -> cycle the line arrives in L1
      self.inflightL2 = {}   # line -> cycle the line arrives in L2

      for name, r in regions.items():
         if r.get('warm'):
            self.warm(bases[name], int(r['size']))
      self.events = []       # (rnd, kind, level, fbWait, l2qWait, latency, t) per modelled access
      self.blockedCycles = set()   # cycles in which a ready load/prefetch could not dispatch (no free FB)

   # --- cache state ---------------------------------------------------------------------------------
   def _fillL2(self, line):
      victim = self.L2.insert(line)
      if victim is not None:
         self.LLC.insert(victim)       # non-inclusive LLC: L2 victims go to the LLC

   def _fillL1(self, line):
      self.L1.insert(line)

   def warm(self, base, size):
      """Touch every line of a region once (in address order), as an untimed warm-up lap would."""
      nLines = size // LINE
      if nLines > 4 * (self.cfg['LLCSize'] // LINE):
         return   # far larger than the LLC: warming leaves nothing useful behind
      first = base // LINE
      for line in range(first, first + nLines):
         self._fillL2(line)
         self._fillL1(line)

   # --- addresses -----------------------------------------------------------------------------------
   # The concrete addresses of an instruction instance (uiCA InstrInstance) come from the execution.
   def covers(self, instrI):
      return bool(getattr(instrI, 'memAddrs', None))

   def addressOf(self, instrI):
      return instrI.memAddrs[0]

   # --- timing --------------------------------------------------------------------------------------
   def needsFB(self, instrI, t):
      """would this access allocate a fill buffer if it were dispatched in cycle t?"""
      instr = instrI.instr
      line = self.addressOf(instrI) // LINE
      if self.inflightL1.get(line, -1) > t:
         return False                                   # merges with the miss in flight
      if self.L1.contains(line):
         return False                                   # L1 hit
      hint = prefetchHintOf(instr)
      reqType = 'ld' if hint is None else ('t0' if hint in ('t0', 'nta') else 't2')
      return self.cfg['fbServiceTime'].get(reqType) != 0

   def canIssue(self, instrI, t):
      """False while the access needs a fill buffer and all of them are busy (the uop must stay in the
      scheduler and retry: the FB station admits nothing while full)"""
      if self.bypass:
         return True   # with the bypass, waiting requests sit in the load buffer, not the scheduler
      return (not self.needsFB(instrI, t)) or (not self.FB.isFull(t))

   def noteBlocked(self, t):
      self.blockedCycles.add(t)

   def noteForwarded(self, rnd, t):
      """a covered load that got its data from an older store in the store buffer (no cache access)"""
      self.events.append((rnd, 'ld', 'SB', 0, 0, None, t))

   def access(self, instrI, t, isPrefetch, prefetchHint, firstTry=None):
      """-> (dataReady, uopDone); dataReady is None for prefetches. firstTry: first cycle the uop was
      blocked for want of an FB (fbWait statistics include that time)."""
      c = self.cfg
      rnd = instrI.rnd
      line = self.addressOf(instrI) // LINE
      toL1 = (not isPrefetch) or prefetchHint in ('t0', 'nta')
      reqType = 'ld' if not isPrefetch else ('t0' if toL1 else 't2')
      svc = c['fbServiceTime'].get(reqType)
      def fbRelease(fbStart, arrive):                  # FB station: service time, capped at the data arrival
         return arrive if svc is None else min(fbStart + svc, arrive)
      level, fbWait, l2qWait = 'L1', 0, 0

      if self.L1.lookup(line) and self.inflightL1.get(line, -1) <= t:
         ready = t + c['L1Lat']
      elif line in self.inflightL1 and self.inflightL1[line] > t:
         level = 'FB'                                   # merges with the miss already in flight
         ready = max(t + c['L1Lat'], self.inflightL1[line])
      else:
         inL2 = self.L2.lookup(line)
         l2Arrive = self.inflightL2.get(line, -1)
         if inL2 or l2Arrive > t:
            level = 'L2'
            if toL1:                                      # L2 -> L1 refill: queues for an FB
               fbStart = self.FB.grant(t)
               ready = max(fbStart + c['L2Lat'], l2Arrive + (c['L2Lat'] - c['L1Lat']))
               self.FB.hold(fbRelease(fbStart, ready), fbStart)
               fbWait = fbStart - t
            else:                                         # prefetcht1/t2 to a line already in (or on its way to) L2
               ready = max(t, l2Arrive)
         else:
            inLLC = self.LLC.lookup(line)
            level = 'LLC' if inLLC else 'DRAM'
            holdFB = (svc is None) or svc > 0
            # FB station first; the L2Q station from the FB grant until the data returns
            def missTime(fbStart):
               def relQ(qStart):
                  if inLLC:
                     return qStart + c['LLCLat']
                  dStart = max(qStart, self.dramNextFree)    # DRAM bandwidth: one line per service time
                  self.dramNextFree = dStart + self.dramServiceTime
                  return dStart + c['DRAMLat']
               qStart, done = self.L2Q.acquire(fbStart, relQ)
               missTime.l2qWait = qStart - fbStart
               return done
            if holdFB and self.bypass and self.FB.isFull(t) and (not self.BQ.isFull(t)):
               if (not toL1) and self.L2Q.isFull(t):      # prefetcht1/t2 with no FB and no L2Q entry: dropped
                  self.events.append((rnd, 'pf' + prefetchHint, 'drop', 0, 0, None, t))
                  return None, t + 1
               arrive = missTime(t)                       # the request leaves for L2/uncore now
               self.BQ.hold(arrive - (c['L2Lat'] - c['L1Lat']))   # bypass entry until the data reaches L2
               if toL1:                                   # FB later, for the L2 -> L1 step; held until the data arrives
                  fbStart, ready = self.FB.acquire(t, lambda st: max(st + c['L2Lat'], arrive))
                  fbWait = fbStart - t
               else:                                      # prefetcht1/t2: the line only goes to L2, no FB
                  ready = arrive
               level += '*'                               # marks: issued without an FB
            elif holdFB:
               fbStart = self.FB.grant(t)
               ready = missTime(fbStart)
               arrive = ready if toL1 else ready - (c['L2Lat'] - c['L1Lat'])   # at L1 / at L2
               self.FB.hold(fbRelease(fbStart, arrive), fbStart)
               fbWait = fbStart - t
            else:
               fbStart, ready = t, missTime(t)
            l2qWait = missTime.l2qWait
            if inLLC:
               self.LLC.lookup(line)
            self._fillL2(line)
            self.inflightL2[line] = ready - (c['L2Lat'] - c['L1Lat'])
         if toL1:
            self._fillL1(line)
            self.inflightL1[line] = ready

      ready = math.ceil(ready)       # uiCA schedules in whole cycles (the DRAM server works in fractions)
      fbWait = math.ceil(fbWait)
      done = t + fbWait + 1
      if firstTry is not None:       # time spent blocked in the scheduler before dispatch
         fbWait += t - firstTry
      if isPrefetch:
         self.events.append((rnd, 'pf' + prefetchHint, level, fbWait, l2qWait, None, t))
         return None, done
      self.events.append((rnd, 'ld', level, fbWait, l2qWait, ready - t, t))
      return ready, ready

   # --- reporting -----------------------------------------------------------------------------------
   def summary(self, firstRnd, lastRnd):
      ev = [e for e in self.events if firstRnd <= e[0] <= lastRnd]
      nIter = max(1, lastRnd - firstRnd + 1)
      out = {'iterations': nIter, 'per_iter': {}, 'avg': {}}
      if ev:
         t0, t1 = min(e[6] for e in ev), max(e[6] for e in ev)
         # cycles in which all FBs are occupied (from the occupancy intervals; sweep over start/end events)
         ev2 = sorted([(max(a, t0), 1) for a, b in self.fbLog if b > t0 and a < t1] +
                      [(min(b, t1), -1) for a, b in self.fbLog if b > t0 and a < t1])
         occ, last, full = 0, t0, 0
         for x, d in ev2:
            if occ >= self.cfg['nFB']:
               full += x - last
            occ += d
            last = x
         out['avg']['fb_full_frac'] = full / max(1, t1 - t0)   # cf. l1d_pend_miss.fb_full / cycles
         nBlocked = sum(1 for c in self.blockedCycles if t0 <= c <= t1)
         out['avg']['dispatch_blocked_frac'] = nBlocked / max(1, t1 - t0)
         out['window_cycles'] = (t0, t1)
      kinds = sorted({e[1] for e in ev})
      for k in kinds:
         ek = [e for e in ev if e[1] == k]
         for lvl in ('SB', 'L1', 'FB', 'L2', 'LLC', 'DRAM', 'LLC*', 'DRAM*', 'drop'):
            n = sum(1 for e in ek if e[2] == lvl)
            if n:
               out['per_iter'][f'{k}_{lvl}'] = n / nIter
         out['avg'][f'{k}_fb_wait'] = sum(e[3] for e in ek) / max(1, len(ek))
         out['avg'][f'{k}_l2q_wait'] = sum(e[4] for e in ek) / max(1, len(ek))
         lat = [e[5] for e in ek if e[5] is not None]
         if lat:
            out['avg'][f'{k}_latency'] = sum(lat) / len(lat)
      return out


def prefetchHintOf(instr):
   s = instr.instrStr.upper()
   for h in ('PREFETCHT0', 'PREFETCHT1', 'PREFETCHT2', 'PREFETCHNTA', 'PREFETCHW'):
      if s.startswith(h):
         return {'PREFETCHT0': 't0', 'PREFETCHT1': 't1', 'PREFETCHT2': 't2', 'PREFETCHNTA': 'nta', 'PREFETCHW': 't0'}[h]
   return None
