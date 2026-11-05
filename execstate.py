"""Execution-driven input for uiCA (-state state.json).

The state file gives the machine's initial state: register values and the contents of memory regions. A functional
x86-64 emulator (Unicorn) runs the code from that state. Each executed instruction yields
    (instruction index, effective addresses of its memory operands, offset of the next instruction)
so the data decides how many iterations run, which branches are taken and which addresses are touched.
uiCA's timing model (front end, scheduler, memsys caches/fill buffers/DRAM) consumes this stream.

State file (JSON); numbers may be ints or strings ("0x40", "4G", "64M", "16K"); an address may be
"<region>" or "<region>+<offset>":
{
  "config":    "EMR_d760" or {...memsys overrides...},       (memory-system parameters, memsys.PRESETS)
  "registers": {"rax": "buf", "rsi": 1000, "rdi": "buf", "rcx": 0, "xmm0": "0x...", "rflags": "0x202"},
  "memory": {
     "buf": {"base": "0x100000000000",                         (optional; page aligned; default (i+1) << 44)
             "size": "4G",                                     (multiple of 4 KiB)
             "warm": false,                                    (true: start with the region in the caches)
             "init": [ <layer>, ... ]}                         (applied in order; later layers win; default zeros)
  },
  "stack": {"size": "64K"},                                    (optional; rsp = top - 8, and [rsp] holds the end
                                                                address, so a final `ret` ends the program)
  "run": {"loopHead": 0,          offset in the code where a new iteration ("round") starts (default 0)
          "maxRounds": null,      stop after this many rounds (default: run until the program ends)
          "maxInstructions": 10000000}
}
Memory init layers (contents are generated lazily, page by page, so a region may be far larger than RAM):
  {"kind": "fill",   "value": V, "elem": 8}                 every elem-byte element = V
  {"kind": "random", "seed": S, "elem": 8}                  element i = splitmix64(seed, i)
  {"kind": "values", "offset": O, "elem": 8, "data": [..]}  explicit elements from byte offset O (values may
                                                            be addresses, e.g. "buf+0x40")
  {"kind": "file",   "path": P, "offset": O}                raw bytes of file P at byte offset O (relative
                                                            paths: relative to the state file)
  {"kind": "chase",  "seed": S, "stride": 64, "offset": 0}  one cyclic pointer chain through every node
                                                            (node k at offset + k*stride, nodes = size/stride, a
                                                            power of two): the qword at each node holds the
                                                            absolute address of the next node; the order is a
                                                            random permutation of the nodes (Permutation)
The program ends when execution leaves the code (falls off its end, or returns to the end address), or at a
limit in "run". The code is the instruction list uiCA disassembled, placed contiguously at CODE_BASE.
"""
import json, os, re

from unicorn import Uc, UcError, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE, UC_PROT_ALL
from unicorn import x86_const

PAGE = 4096
MASK64 = (1 << 64) - 1
CODE_BASE = 0x400000
STACK_BASE = 0x7f0000000000

# --- value parsing -----------------------------------------------------------------------------------
_SUFFIX = {'K': 1 << 10, 'M': 1 << 20, 'G': 1 << 30, 'T': 1 << 40}


def parseNum(v):
   if isinstance(v, int):
      return v
   s = str(v).strip()
   if s and s[-1].upper() in _SUFFIX:
      return int(s[:-1], 0) * _SUFFIX[s[-1].upper()]
   return int(s, 0)


def parseValue(v, bases):
   """int, numeric string, '<region>', '<region>+<off>' or '<region>-<off>'"""
   if isinstance(v, int):
      return v
   m = re.fullmatch(r'\s*([A-Za-z_]\w*)\s*(?:([+-])\s*(\S+))?\s*', str(v))
   if m and m.group(1) in bases:
      off = parseNum(m.group(3)) if m.group(3) else 0
      return bases[m.group(1)] + (off if m.group(2) != '-' else -off)
   return parseNum(v)


# --- deterministic generators ------------------------------------------------------------------------
def splitmix64(x):
   x = (x + 0x9E3779B97F4A7C15) & MASK64
   x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
   x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & MASK64
   return x ^ (x >> 31)


_PERM_CONSTS = (0x9E3779B97F4A7C15, 0xBF58476D1CE4E5B9, 0x94D049BB133111EB)


class Permutation:
   """bijection on [0, 2^bits) (multiply / xor-shift rounds), with its inverse"""
   def __init__(self, bits, seed):
      self.bits, self.m, self.seed = bits, (1 << bits) - 1, seed
      self.shift = max(1, bits // 2)
      self.inv = [pow(c & self.m, -1, 1 << bits) if bits else 0 for c in _PERM_CONSTS]

   def fwd(self, i):
      if self.bits == 0:
         return 0
      x = (i ^ self.seed) & self.m
      for c in _PERM_CONSTS:
         x = (x * c) & self.m
         x ^= x >> self.shift
      return x

   def back(self, x):
      if self.bits == 0:
         return 0
      for c in reversed(range(len(_PERM_CONSTS))):
         y = x                                       # undo x ^= x >> s
         for _ in range(self.bits // self.shift + 1):
            y = x ^ (y >> self.shift)
         x = (y * self.inv[c]) & self.m
      return (x ^ self.seed) & self.m


# --- memory regions ----------------------------------------------------------------------------------
class Region:
   """A memory region whose pages are materialized on first touch from the init layers."""
   def __init__(self, name, base, size, layers, bases, stateDir):
      self.name, self.base, self.size = name, base, size
      self.pages = {}            # page index -> bytearray
      self.layers = [self._layer(l, bases, stateDir) for l in layers]

   def _layer(self, l, bases, stateDir):
      kind = l.get('kind')
      if kind == 'fill':
         elem, v = int(l.get('elem', 8)), parseValue(l['value'], bases)
         chunk = (v & ((1 << (8 * elem)) - 1)).to_bytes(elem, 'little') * (PAGE // elem)
         return lambda buf, off: buf.__setitem__(slice(0, PAGE), chunk)
      if kind == 'random':
         elem, seed = int(l.get('elem', 8)), parseNum(l.get('seed', 1))
         mask = (1 << (8 * elem)) - 1
         def apply(buf, off):
            for j in range(0, PAGE, elem):
               buf[j:j + elem] = (splitmix64((seed << 40) ^ ((off + j) // elem)) & mask).to_bytes(elem, 'little')
         return apply
      if kind == 'values':
         elem, start = int(l.get('elem', 8)), parseNum(l.get('offset', 0))
         data = b''.join((parseValue(v, bases) & ((1 << (8 * elem)) - 1)).to_bytes(elem, 'little') for v in l['data'])
         return self._bytesLayer(start, data)
      if kind == 'file':
         path = l['path'] if os.path.isabs(l['path']) else os.path.join(stateDir, l['path'])
         with open(path, 'rb') as f:
            return self._bytesLayer(parseNum(l.get('offset', 0)), f.read())
      if kind == 'chase':
         stride, start = parseNum(l.get('stride', 64)), parseNum(l.get('offset', 0))
         n = (self.size - start) // stride
         bits = n.bit_length() - 1
         if n < 1 or (1 << bits) != n or stride < 8 or PAGE % stride and stride % PAGE:
            raise ValueError('chase: the number of nodes ({}) must be a power of two'.format(n))
         perm = Permutation(bits, parseNum(l.get('seed', 1)))
         base = self.base + start
         def apply(buf, off):
            first = max(0, -(-(off - start) // stride))
            k = first
            while start + k * stride < off + PAGE and k < n:
               nxt = perm.fwd((perm.back(k) + 1) % n)          # successor of node k along the cycle
               o = start + k * stride - off
               buf[o:o + 8] = (base + nxt * stride).to_bytes(8, 'little')
               k += 1
         self.chasePerm = perm
         return apply
      raise ValueError('unknown memory init kind: {}'.format(kind))

   @staticmethod
   def _bytesLayer(start, data):
      def apply(buf, off):
         lo, hi = max(off, start), min(off + PAGE, start + len(data))
         if lo < hi:
            buf[lo - off:hi - off] = data[lo - start:hi - start]
      return apply

   def page(self, p):
      buf = self.pages.get(p)
      if buf is None:
         buf = bytearray(PAGE)
         for apply in self.layers:
            apply(buf, p * PAGE)
         self.pages[p] = buf
      return buf

   def read(self, off, size):
      out = bytearray()
      while size:
         p, o = divmod(off, PAGE)
         n = min(size, PAGE - o)
         out += self.page(p)[o:o + n]
         off, size = off + n, size - n
      return bytes(out)

   def write(self, off, data):
      while data:
         p, o = divmod(off, PAGE)
         n = min(len(data), PAGE - o)
         self.page(p)[o:o + n] = data[:n]
         off, data = off + n, data[n:]


# --- the emulator ------------------------------------------------------------------------------------
def _ucReg(name):
   r = getattr(x86_const, 'UC_X86_REG_' + name.upper(), None)
   if r is None:
      raise ValueError('unknown register: ' + name)
   return r


class Execution:
   """Runs the code from the initial state; rounds() yields, per round, a list of
   (instruction index, [effective addresses], offset of the next instruction)."""

   CHUNK = 4096

   def __init__(self, state, instructions, stateDir='.'):
      self.state = state
      self.instructions = instructions
      self.offsets, off = [], 0
      for instr in instructions:
         self.offsets.append(off)
         off += len(instr.opcode) // 2
      self.codeLen = off
      self.offToIdx = {o: i for i, o in enumerate(self.offsets)}
      self.end = CODE_BASE + self.codeLen

      run = state.get('run', {})
      self.loopHead = parseNum(run.get('loopHead', 0))
      self.maxRounds = run.get('maxRounds')
      self.maxInstructions = parseNum(run.get('maxInstructions', 10_000_000))

      uc = self.uc = Uc(UC_ARCH_X86, UC_MODE_64)
      codeSize = -(-max(self.codeLen, 1) // PAGE) * PAGE + PAGE
      uc.mem_map(CODE_BASE, codeSize, UC_PROT_ALL)
      uc.mem_write(CODE_BASE, b''.join(bytes.fromhex(i.opcode) for i in instructions))

      # regions: bases first (values may refer to any region), then contents
      mem = state.get('memory', {})
      self.bases = {}
      for i, (name, r) in enumerate(mem.items()):
         self.bases[name] = parseNum(r['base']) if 'base' in r else (i + 1) << 44
      self.regions = {}
      for name, r in mem.items():
         base, size = self.bases[name], parseNum(r['size'])
         if base % PAGE or size % PAGE or size <= 0:
            raise ValueError('region {}: base and size must be multiples of 4 KiB'.format(name))
         reg = Region(name, base, size, r.get('init', []), self.bases, stateDir)
         reg.warm = bool(r.get('warm', False))
         self.regions[name] = reg
         uc.mmio_map(base, size, self._mmioRead, reg, self._mmioWrite, reg)

      regs = {k.lower(): v for k, v in state.get('registers', {}).items()}
      stack = state.get('stack')
      if stack is not None or 'rsp' not in regs:
         ssize = parseNum((stack or {}).get('size', '64K'))
         uc.mem_map(STACK_BASE, ssize, UC_PROT_ALL)
         rsp = STACK_BASE + ssize - 64
         uc.mem_write(rsp, self.end.to_bytes(8, 'little'))     # a `ret` at the end returns out of the code
         regs.setdefault('rsp', rsp)
      for name, v in regs.items():
         uc.reg_write(_ucReg(name), parseValue(v, self.bases))

      # per instruction: how to compute the effective address of each memory operand
      self.eaInfo = []
      for instr in instructions:
         ops, seen = [], set()
         for memOp in list(instr.inputMemOperands) + list(instr.outputMemOperands):
            a = memOp.memAddr
            key = (a.get('base'), a.get('index'), a.get('scale', 1), a.get('disp', 0))
            if key in seen:
               continue                                    # e.g. the load and the store of `add [m], r`
            seen.add(key)
            base, index = a.get('base'), a.get('index')
            ops.append((None if base in (None, 'RIP') else _ucReg(base), base == 'RIP',
                        None if index is None else _ucReg(index), int(a.get('scale', 1)), int(a.get('disp', 0))))
         self.eaInfo.append(ops)
      self.instrLen = [len(i.opcode) // 2 for i in instructions]

      self.pending = []          # records of the current chunk
      self.nInstructions = 0
      self.finished = False
      self.pc = CODE_BASE
      uc.hook_add(UC_HOOK_CODE, self._onInstr)

   # memory-mapped regions -----------------------------------------------------------------------------
   @staticmethod
   def _mmioRead(uc, offset, size, reg):
      return int.from_bytes(reg.read(offset, size), 'little')

   @staticmethod
   def _mmioWrite(uc, offset, size, value, reg):
      reg.write(offset, (value & ((1 << (8 * size)) - 1)).to_bytes(size, 'little'))

   def readMem(self, addr, size):
      for reg in self.regions.values():
         if reg.base <= addr < reg.base + reg.size:
            return reg.read(addr - reg.base, size)
      return bytes(self.uc.mem_read(addr, size))

   def reg(self, name):
      return self.uc.reg_read(_ucReg(name))

   # tracing -------------------------------------------------------------------------------------------
   def _onInstr(self, uc, address, size, user):
      off = address - CODE_BASE
      idx = self.offToIdx.get(off)
      if idx is None:
         raise RuntimeError('execution reached 0x{:x}, which is not the start of an instruction of the code'.format(address))
      addrs = []
      for baseReg, isRip, indexReg, scale, disp in self.eaInfo[idx]:
         ea = disp
         if isRip:
            ea += address + self.instrLen[idx]
         elif baseReg is not None:
            ea += uc.reg_read(baseReg)
         if indexReg is not None:
            ea += uc.reg_read(indexReg) * scale
         addrs.append(ea & MASK64)
      self.pending.append((idx, addrs))

   def _runChunk(self):
      """execute up to CHUNK instructions; -> list of (idx, addrs, nextOffset)"""
      self.pending = []
      if self.finished:
         return []
      budget = min(self.CHUNK, self.maxInstructions - self.nInstructions)
      if budget <= 0:
         self.finished = True
         return []
      try:
         self.uc.emu_start(self.pc, self.end, count=budget)
      except UcError as e:
         where = self.pending[-1][0] if self.pending else None
         raise RuntimeError('emulation failed at instruction {} ({}): {}'.format(
            where, self.instructions[where].asm if where is not None else '?', e))
      self.pc = self.uc.reg_read(x86_const.UC_X86_REG_RIP)
      recs = self.pending
      self.nInstructions += len(recs)
      if not (CODE_BASE <= self.pc < self.end) or not recs:
         self.finished = True
      out = []
      for j, (idx, addrs) in enumerate(recs):
         nxt = recs[j + 1][0] if j + 1 < len(recs) else None
         nextOff = self.offsets[nxt] if nxt is not None else (self.pc - CODE_BASE)
         out.append((idx, addrs, nextOff))
      return out

   def records(self):
      while True:
         chunk = self._runChunk()
         if not chunk:
            return
         yield from chunk

   def rounds(self):
      cur, nRounds = [], 0
      for rec in self.records():
         if self.offsets[rec[0]] == self.loopHead and cur:
            yield cur
            nRounds += 1
            cur = []
            if self.maxRounds is not None and nRounds >= int(self.maxRounds):
               self.finished = True
               return
         cur.append(rec)
      if cur:
         yield cur


def loadState(path):
   with open(path) as f:
      return json.load(f)
