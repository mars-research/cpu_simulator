"""Memory-system model for uiCA: non-constant load latency.

Stock uiCA gives every load a fixed latency (an L1 hit). With a memory spec (-memSpec), every load uop
(including software prefetches, which uiCA represents as load uops without an output register) of an
instruction that the spec covers asks this model, at dispatch, when its data is ready:

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
prefetch completes (frees its uop) as soon as it has its FB. Option fbFullL2Hint (off by default): an
L1 miss that finds every FB busy enters the L2Q at once and takes an FB later for the L2 -> L1 step
only (statistics mark such accesses LLC* / DRAM*). Caches are set-associative LRU; the LLC is
non-inclusive (fills from DRAM go to L2/L1, L2 victims go to the LLC). Stores are not modelled.

Addresses: uiCA only knows symbolic addresses, so the spec gives every covered memory instruction an
address pattern over a named region; instance `rnd` (the loop iteration) of instruction `i` accesses
pattern_i(rnd + shift_i). Patterns:
    random  {seed}           uniformly random line in the region
    perm    {seed}           a random bijection over the region's lines: every line once per
                             lap of size/64 iterations (pointer chase over a cyclic permutation)
    stride  {stride, start}  start + n * stride (mod region size)
    same    {offset}         one fixed address
    trace   {addrs}          explicit byte offsets into the region, cycled
`shift` (default 0) pairs instructions: a prefetch with shift D and a load with shift 0 and the same
pattern/seed access the same line D iterations apart, exactly like `prefetch [f(i+D)]; load [f(i)]`.

Spec file (JSON):
{
  "config":   "EMR_d760" or {...overrides...} or {"preset": "EMR_d760", ...overrides...},
  "regions":  {"buf": {"size": 4294967296, "warm": false}},
  "accesses": {"<instr index>": {"region": "buf", "pattern": "random", "seed": 1, "shift": 0}, ...}
}
Instruction indices are positions in uiCA's instruction list (run with -memSpec and a spec without
"accesses" to have them printed). Instructions not in "accesses" keep stock behaviour (L1 hit).
"""
import heapq, json, math
from collections import OrderedDict

LINE = 64
MASK64 = (1 << 64) - 1

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
      'fbServiceTime': {'ld': 244, 't0': 172, 't2': 122},   # FB station service time per request type, cycles
                               # (None = until the data arrives). Fitted by tests/fit_fb.py with stall-at-dispatch FB admission (results/fit_fb_stall.json)
      'nL2Q': 48,              # L2Q station servers: outstanding L2 misses per core (documented 48;
                               # tests/mix_t2x2: prefetcht2 alone sustains ~41)
      'fbFullL2Hint': False,   # see the module docstring
      'DRAMLinesPerCycle': 2.08,   # 8 ch DDR5-5200 = 332.8 GB/s at 2.5 GHz; one socket
   },
}


def splitmix64(x):
   x = (x + 0x9E3779B97F4A7C15) & MASK64
   x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
   x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & MASK64
   return x ^ (x >> 31)


class AddressPattern:
   def __init__(self, spec, region, regionBase):
      self.kind = spec.get('pattern', 'random')
      self.shift = int(spec.get('shift', 0))
      self.seed = int(spec.get('seed', 1))
      self.size = int(region['size'])
      self.base = regionBase
      self.nLines = max(1, self.size // LINE)
      if self.kind == 'perm':
         self.bits = self.nLines.bit_length() - 1
         if (1 << self.bits) != self.nLines:
            raise ValueError('perm needs a power-of-two number of lines')
      elif self.kind == 'stride':
         self.stride, self.start = int(spec['stride']), int(spec.get('start', 0))
      elif self.kind == 'same':
         self.offset = int(spec.get('offset', 0))
      elif self.kind == 'trace':
         self.addrs = [int(a) for a in spec['addrs']]
      elif self.kind != 'random':
         raise ValueError('unknown pattern ' + self.kind)

   def _perm(self, i):
      # bijection on `bits` bits: multiply by an odd constant / xor-shift right, three rounds
      b, m = self.bits, self.nLines - 1
      if b == 0:
         return 0
      x = (i ^ self.seed) & m
      for c in (0x9E3779B97F4A7C15, 0xBF58476D1CE4E5B9, 0x94D049BB133111EB):
         x = (x * c) & m
         x ^= x >> max(1, b // 2)
      return x

   def address(self, rnd):
      n = rnd + self.shift
      if self.kind == 'random':
         off = (splitmix64((self.seed << 40) ^ n) % self.nLines) * LINE
      elif self.kind == 'perm':
         off = self._perm(n) * LINE
      elif self.kind == 'stride':
         off = (self.start + n * self.stride) % self.size
      elif self.kind == 'same':
         off = self.offset
      else:
         off = self.addrs[n % len(self.addrs)]
      return self.base + off


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
   def __init__(self, n):
      self.n = n
      self.busy = []   # heap of release times

   def acquire(self, t, releaseFn):
      while self.busy and self.busy[0] <= t:
         heapq.heappop(self.busy)
      start = t
      if len(self.busy) >= self.n:
         start = max(t, heapq.heappop(self.busy))
      release = releaseFn(start)
      heapq.heappush(self.busy, release)
      return start, release

   def grant(self, t):
      """start time >= t of the earliest free server; the caller must then hold(release)"""
      while self.busy and self.busy[0] <= t:
         heapq.heappop(self.busy)
      if len(self.busy) >= self.n:
         return max(t, heapq.heappop(self.busy))
      return t

   def hold(self, release):
      heapq.heappush(self.busy, release)

   def isFull(self, t):
      return sum(1 for r in self.busy if r > t) >= self.n

   def occupancy(self, t):
      return sum(1 for r in self.busy if r > t)


class MemSystem:
   def __init__(self, spec, instructions):
      cfg = spec.get('config', 'EMR_d760')
      if isinstance(cfg, str):
         cfg = {'preset': cfg}
      self.cfg = dict(PRESETS[cfg.get('preset', 'EMR_d760')])
      self.cfg.update({k: v for k, v in cfg.items() if k != 'preset'})
      c = self.cfg
      self.L1, self.L2, self.LLC = Cache(c['L1Size'], c['L1Assoc']), Cache(c['L2Size'], c['L2Assoc']), Cache(c['LLCSize'], c['LLCAssoc'])
      self.FB, self.L2Q = SlotResource(c['nFB']), SlotResource(c['nL2Q'])
      self.dramNextFree = 0.0
      self.dramServiceTime = 1.0 / c['DRAMLinesPerCycle']
      self.inflightL1 = {}   # line -> cycle the line arrives in L1
      self.inflightL2 = {}   # line -> cycle the line arrives in L2

      regions = spec.get('regions', {})
      bases = {name: (i + 1) << 44 for i, name in enumerate(regions)}   # disjoint, far apart
      self.patterns = {}
      for idx, a in spec.get('accesses', {}).items():
         instr = instructions[int(idx)]
         self.patterns[instr] = AddressPattern(a, regions[a['region']], bases[a['region']])
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

   # --- timing --------------------------------------------------------------------------------------
   def needsFB(self, instr, rnd, t):
      """would this access allocate a fill buffer if it were dispatched in cycle t?"""
      line = self.patterns[instr].address(rnd) // LINE
      if self.inflightL1.get(line, -1) > t:
         return False                                   # merges with the miss in flight
      if self.L1.contains(line):
         return False                                   # L1 hit
      hint = prefetchHintOf(instr)
      reqType = 'ld' if hint is None else ('t0' if hint in ('t0', 'nta') else 't2')
      return self.cfg['fbServiceTime'].get(reqType) != 0

   def canIssue(self, instr, rnd, t):
      """False while the access needs a fill buffer and all of them are busy (the uop must stay in the
      scheduler and retry: the FB station admits nothing while full)"""
      if self.cfg.get('fbFullL2Hint'):
         return True
      return (not self.needsFB(instr, rnd, t)) or (not self.FB.isFull(t))

   def noteBlocked(self, t):
      self.blockedCycles.add(t)

   def noteForwarded(self, rnd, t):
      """a covered load that got its data from an older store in the store buffer (no cache access)"""
      self.events.append((rnd, 'ld', 'SB', 0, 0, None, t))

   def access(self, instr, rnd, t, isPrefetch, prefetchHint, firstTry=None):
      """-> (dataReady, uopDone); dataReady is None for prefetches. firstTry: first cycle the uop was
      blocked for want of an FB (fbWait statistics include that time)."""
      c = self.cfg
      line = self.patterns[instr].address(rnd) // LINE
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
            fbStart = self.FB.grant(t)
            ready = max(fbStart + c['L2Lat'], l2Arrive + (c['L2Lat'] - c['L1Lat']))
            self.FB.hold(fbRelease(fbStart, ready if toL1 else fbStart))
            fbWait = fbStart - t
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
            if holdFB and c.get('fbFullL2Hint') and self.FB.isFull(t):
               arrive = missTime(t)                       # the request leaves for L2/uncore now
               if toL1:                                   # FB later, for the L2 -> L1 step only
                  fbStart, ready = self.FB.acquire(t, lambda st: max(st + c['L2Lat'], arrive))
                  fbWait = fbStart - t
               else:                                      # prefetcht1/t2: the line only goes to L2
                  ready = arrive
               level += '*'                               # marks: issued without an FB
            elif holdFB:
               fbStart = self.FB.grant(t)
               ready = missTime(fbStart)
               arrive = ready if toL1 else ready - (c['L2Lat'] - c['L1Lat'])   # at L1 / at L2
               self.FB.hold(fbRelease(fbStart, arrive))
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
         nBlocked = sum(1 for c in self.blockedCycles if t0 <= c <= t1)
         out['avg']['fb_full_frac'] = nBlocked / max(1, t1 - t0)   # cf. l1d_pend_miss.fb_full / cycles
         out['window_cycles'] = (t0, t1)
      kinds = sorted({e[1] for e in ev})
      for k in kinds:
         ek = [e for e in ev if e[1] == k]
         for lvl in ('SB', 'L1', 'FB', 'L2', 'LLC', 'DRAM', 'LLC*', 'DRAM*'):
            n = sum(1 for e in ek if e[2] == lvl)
            if n:
               out['per_iter'][f'{k}_{lvl}'] = n / nIter
         out['avg'][f'{k}_fb_wait'] = sum(e[3] for e in ek) / max(1, len(ek))
         out['avg'][f'{k}_l2q_wait'] = sum(e[4] for e in ek) / max(1, len(ek))
         lat = [e[5] for e in ek if e[5] is not None]
         if lat:
            out['avg'][f'{k}_latency'] = sum(lat) / len(lat)
      return out


def loadSpec(path):
   with open(path) as f:
      return json.load(f)


def prefetchHintOf(instr):
   s = instr.instrStr.upper()
   for h in ('PREFETCHT0', 'PREFETCHT1', 'PREFETCHT2', 'PREFETCHNTA', 'PREFETCHW'):
      if s.startswith(h):
         return {'PREFETCHT0': 't0', 'PREFETCHT1': 't1', 'PREFETCHT2': 't2', 'PREFETCHNTA': 'nta', 'PREFETCHW': 't0'}[h]
   return None
