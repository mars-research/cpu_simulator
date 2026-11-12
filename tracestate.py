"""Execution-driven input for uiCA from a native trace (ptrace/steptrace), instead of the Unicorn emulator.

Use when the code cannot run in Unicorn (AVX2/AVX-512): steptrace single-steps the real program on the real
CPU and records, for every instruction executed inside one function, RIP and the 16 GPRs before it runs.
This module turns that into the same stream execstate.Execution produces
    (instruction index, effective addresses of its memory operands, offset of the next instruction)
so uiCA's front end, scheduler and memory model run unchanged.

State file (uiCA -state):
{
  "config": "EMR_d760",                    memory-system preset/overrides, as in execstate
  "trace":  {"file": "D_best.trc"},        steptrace output (relative to the state file)
  "run":    {"loopHead": 224, "maxRounds": null, "skipInstructions": 0, "maxInstructions": 10000000}
}
The code given to uiCA must be the traced function's bytes (e.g. extracted with -raw), so that instruction i
starts at offset offsets[i] from the traced range's start.

Notes
- Only instructions inside the function are recorded. Leaving it (ret, or a call out) and coming back is
  passed on as a taken branch to the next recorded instruction; the caller's instructions are not simulated.
- Effective addresses come from base + index*scale + disp with the recorded GPRs (RIP-relative: real RIP).
  VSIB (gather/scatter) operands get no address; masked vector accesses get the full operand's address.
- There are no memory regions: every line starts cold in the memory model (no "warm").
"""
import os, struct

MASK64 = (1 << 64) - 1
REC = 17
_SLOT = {n: i + 1 for i, n in enumerate(['RAX', 'RBX', 'RCX', 'RDX', 'RSI', 'RDI', 'RBP', 'RSP'] +
                                         ['R' + str(k) for k in range(8, 16)])}
_ALIAS = {'EAX': 'RAX', 'EBX': 'RBX', 'ECX': 'RCX', 'EDX': 'RDX', 'ESI': 'RSI', 'EDI': 'RDI', 'EBP': 'RBP', 'ESP': 'RSP'}
_ALIAS.update({'R{}D'.format(k): 'R{}'.format(k) for k in range(8, 16)})


def _slot(reg):
   if reg is None:
      return None
   r = _ALIAS.get(reg.upper(), reg.upper())
   if r not in _SLOT:
      return -1                     # vector index (VSIB) or unknown: no address
   return _SLOT[r]


def readTrace(path):
   with open(path, 'rb') as f:
      if f.read(8) != b'STEPTRC1':
         raise ValueError(path + ': not a steptrace file')
      lo, size, n = struct.unpack('<3Q', f.read(24))
      data = f.read(n * REC * 8)
   return lo, size, n, data


class TraceExecution:
   def __init__(self, state, instructions, stateDir='.'):
      tr = state['trace']
      path = tr['file'] if os.path.isabs(tr['file']) else os.path.join(stateDir, tr['file'])
      self.lo, self.size, n, self.data = readTrace(path)
      self.instructions = instructions
      self.offsets, off = [], 0
      for instr in instructions:
         self.offsets.append(off)
         off += len(instr.opcode) // 2
      self.codeLen = off
      self.offToIdx = {o: i for i, o in enumerate(self.offsets)}
      self.instrLen = [len(i.opcode) // 2 for i in instructions]

      run = state.get('run', {})
      self.loopHead = int(str(run.get('loopHead', 0)), 0)
      self.maxRounds = run.get('maxRounds')
      self.skip = int(run.get('skipInstructions', 0))
      self.nRecords = min(n, self.skip + int(run.get('maxInstructions', 10_000_000)))

      self.eaInfo = []
      for instr in instructions:
         ops, seen = [], set()
         for memOp in list(instr.inputMemOperands) + list(instr.outputMemOperands):
            a = memOp.memAddr
            key = (a.get('base'), a.get('index'), a.get('scale', 1), a.get('disp', 0))
            if key in seen:
               continue
            seen.add(key)
            base, index = a.get('base'), a.get('index')
            ops.append((None if base in (None, 'RIP') else _slot(base), base == 'RIP', _slot(index),
                        int(a.get('scale', 1)), int(a.get('disp', 0))))
         self.eaInfo.append(ops)

      self.regions, self.bases = {}, {}    # no state-file regions: everything starts cold
      self.nInstructions = 0
      self.finished = False
      self.lastRegs = None

   def _rec(self, j):
      return struct.unpack_from('<17Q', self.data, j * REC * 8)

   def records(self):
      prev = None
      for j in range(self.skip, self.nRecords):
         r = self._rec(j)
         off = r[0] - self.lo
         idx = self.offToIdx.get(off)
         if idx is None:
            raise RuntimeError('trace record {}: rip 0x{:x} is not the start of an instruction of the code'.format(j, r[0]))
         addrs = []
         for base, isRip, index, scale, disp in self.eaInfo[idx]:
            if base == -1 or index == -1:
               continue
            ea = disp
            if isRip:
               ea += r[0] + self.instrLen[idx]
            elif base is not None:
               ea += r[base]
            if index is not None:
               ea += r[index] * scale
            addrs.append(ea & MASK64)
         if prev is not None:
            yield (prev[0], prev[1], off)
         prev = (idx, addrs)
         self.lastRegs = r
         self.nInstructions += 1
      if prev is not None:
         yield (prev[0], prev[1], self.codeLen)    # the program ends: fetch leaves the code (as in execstate)
      self.finished = True

   def rounds(self):
      cur, nRounds = [], 0
      for rec in self.records():
         if self.offsets[rec[0]] == self.loopHead and cur:
            if self.maxRounds is not None and nRounds + 1 >= int(self.maxRounds):
               cur[-1] = (cur[-1][0], cur[-1][1], self.codeLen)   # last round: the program ends after it
            yield cur
            nRounds += 1
            cur = []
            if self.maxRounds is not None and nRounds >= int(self.maxRounds):
               self.finished = True
               return
         cur.append(rec)
      if cur:
         yield cur

   def reg(self, name):
      s = _slot(name)
      return 0 if (self.lastRegs is None or s in (None, -1)) else self.lastRegs[s]
