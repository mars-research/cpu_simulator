#!/usr/bin/env python3
"""Checks the execution-driven input (-state, ../execstate.py): the emulated program must compute what the
code computes from the given state (final registers, memory, path, addresses), and uiCA must time exactly
the executed instructions.

    ../.venv/bin/python test_execstate.py

Kernels: build/*.o from kernels/ (./build.sh) and exec_kernels/*.s (assembled here).
"""
import importlib, json, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import xed, execstate
from microArchConfigs import MicroArchConfigs
from instructions import getInstructions

ARCH = MicroArchConfigs['EMR']
PY, UICA = HERE.parent / '.venv' / 'bin' / 'python', HERE.parent / 'uiCA.py'
FAILED = []


def check(name, cond, detail=''):
    print(('PASS ' if cond else 'FAIL ') + name + (('   ' + detail) if detail else ''))
    if not cond:
        FAILED.append(name)


def assemble(k):
    src, obj = HERE / 'build' / f'exec_{k}.uica.s', HERE / 'build' / f'exec_{k}.o'
    src.write_text(f'.intel_syntax noprefix\n.text\n1:\n.include "{HERE / "exec_kernels" / (k + ".s")}"\n')
    subprocess.run(['as', str(src), '-o', str(obj)], check=True)
    return obj


def instrs(obj):
    d = xed.disasFile(str(obj), chip=ARCH.XEDName)
    return getInstructions(d, ARCH, importlib.import_module('instrData.EMR_data'), 0, False, False)


def uica(obj, st):
    """run uiCA -state; -> the -memJson output"""
    with tempfile.TemporaryDirectory() as d:
        sp, mj = Path(d) / 'state.json', Path(d) / 'mem.json'
        sp.write_text(json.dumps(st))
        p = subprocess.run([str(PY), str(UICA), str(obj), '-arch', 'EMR', '-TPonly', '-state', str(sp), '-memJson', str(mj)],
                           capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError(p.stderr[-1500:])
        return json.loads(mj.read_text())


def crc32c(crc, data):
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc


def rnd64(seed, i):
    return execstate.splitmix64((seed << 40) ^ i)


def test_permutation():
    for bits in (0, 1, 5, 10, 26):
        p = execstate.Permutation(bits, 7)
        xs = range(min(1 << bits, 5000))
        check(f'permutation inverse ({bits} bits)', all(p.back(p.fwd(i)) == i for i in xs))
    p = execstate.Permutation(12, 1)
    check('permutation is a bijection', len({p.fwd(i) for i in range(4096)}) == 4096)


def test_chase():
    ins = instrs(HERE / 'build' / 'chase.o')
    n, steps = 1 << 14, 10_000                     # 1 MiB of 64-byte nodes; more steps than one emulator chunk
    p = execstate.Permutation(14, 3)
    st = {'registers': {'rax': 'buf', 'rsi': steps}, 'memory': {'buf': {'size': '1M', 'init': [{'kind': 'chase', 'seed': 3}]}}}
    ex = execstate.Execution(st, ins)
    rounds = list(ex.rounds())
    base = ex.bases['buf']
    x, want = 0, []
    for _ in range(steps):
        want.append(base + x * 64)
        x = p.fwd((p.back(x) + 1) % n)
    check('chase: one round per iteration', len(rounds) == steps and all(len(r) == 3 for r in rounds), f'{len(rounds)} rounds')
    check('chase: load addresses follow the chain', [r[0][1][0] for r in rounds] == want)
    check('chase: final rax / rsi', ex.reg('rax') == base + x * 64 and ex.reg('rsi') == 0)
    check('chase: every node once per lap', len(set(want[:n])) == min(n, steps))


def test_crc_addresses():
    ins = instrs(HERE / 'build' / 'pf_t0.o')
    st = {'registers': {'rdi': 'buf', 'rcx': 0, 'rsi': 300}, 'memory': {'buf': {'size': '4G'}}}
    ex = execstate.Execution(st, ins)
    rounds = list(ex.rounds())
    base = ex.bases['buf']
    line = lambda i: base + ((crc32c(0, i.to_bytes(8, 'little')) & 0x3ffffff) << 6)
    pf = [a for r in rounds for (i, a, _) in r if ins[i].asm.startswith('prefetch')]
    ld = [a for r in rounds for (i, a, _) in r if ins[i].asm.startswith('add r8')]
    check('pf_t0: prefetch addresses = crc32c(i + 32) lines (prefetches access no memory in the emulator)',
          pf == [[line(i + 32)] for i in range(300)])
    check('pf_t0: load addresses = crc32c(i) lines', ld == [[line(i)] for i in range(300)])


def test_branchy():
    obj = assemble('branchy')
    n, seed = 3000, 5
    st = {'registers': {'rdi': 'a', 'rcx': 0, 'rsi': n, 'r8': 0, 'r9': 0},
          'memory': {'a': {'size': '64K', 'warm': True, 'init': [{'kind': 'random', 'seed': seed}]}}}
    vals = [rnd64(seed, i) for i in range(n)]
    odd = [v for v in vals if v & 1]
    out = uica(obj, st)
    x = out['execution']
    regs = x['final_registers']
    check('branchy: final r8 = sum of odd elements, r9 = their count',
          regs['r8'] == sum(odd) & ((1 << 64) - 1) and regs['r9'] == len(odd), f'r9={regs["r9"]} want {len(odd)}')
    jz = next(b for b in x['branches'] if b['asm'].startswith('jz'))
    check('branchy: jz taken for exactly the even elements', jz['executed'] == n and jz['taken'] == n - len(odd),
          f'taken {jz["taken"]}/{jz["executed"]}')
    check('branchy: instructions = 6 per element + 2 per odd one', x['instructions'] == 6 * n + 2 * len(odd),
          f'{x["instructions"]}')
    check('branchy: rounds = elements', x['rounds'] == n)
    return out


def test_probe():
    obj = assemble('probe')
    n = 2000
    st = {'registers': {'rdi': 'a', 'rbx': 'tab', 'rcx': 0, 'rsi': n, 'r8': 0},
          'memory': {'a': {'size': '16K', 'warm': True, 'init': [{'kind': 'random', 'seed': 11}]},
                     'tab': {'size': '4K', 'warm': True, 'init': [{'kind': 'random', 'seed': 12}]}}}
    L = [rnd64(11, i) & 3 for i in range(n)]
    tab = lambda d: rnd64(12, d * 64 // 8)
    want = sum(tab(d) for l in L for d in range(1, l + 1)) & ((1 << 64) - 1)
    out = uica(obj, st)
    x = out['execution']
    check('probe: final r8 = sum over the probed entries', x['final_registers']['r8'] == want)
    check('probe: instructions = 7 + 7 L per element', x['instructions'] == sum(7 + 7 * l for l in L), f'{x["instructions"]}')
    jmp = next(b for b in x['branches'] if b['asm'].startswith('jmp'))
    check('probe: inner-loop back edge executed sum(L) times', jmp['executed'] == sum(L) and jmp['taken'] == sum(L))
    check('probe: rounds = elements (inner iterations stay in their round)', x['rounds'] == n)


def test_counter():
    obj = assemble('counter')
    n = 2000
    st = {'registers': {'rdi': 'c', 'rsi': n},
          'memory': {'c': {'size': '4K', 'warm': True, 'init': [{'kind': 'values', 'data': [100]}]}}}
    out = uica(obj, st)
    x = out['execution']
    check('counter: value passed through memory (rax = 100 + 3n)', x['final_registers']['rax'] == 100 + 3 * n)
    fwd = out['per_iter'].get('ld_SB', 0)
    # per round: the RMW's load and the mov's load; both can only get the value from the previous store
    check('counter: loads forwarded from the store buffer', fwd > 0.9, f'ld_SB {fwd:.2f} per round, TP {out["TP"]}')
    return out


def test_timing():
    """end to end: the chase states reproduce the calibrated load-to-use latencies"""
    gt = json.loads((HERE / 'ground_truth.json').read_text())
    for name in ('chase_l2', 'chase_dram'):
        st = json.loads((HERE / 'states' / f'{name}.json').read_text())
        tp = uica(HERE / 'build' / 'chase.o', st)['TP']
        m = gt[name]['cycles_per_iter']
        check(f'{name}: {tp} cycles/round vs {m:.2f} measured', abs(tp / m - 1) < 0.02)


if __name__ == '__main__':
    test_permutation()
    test_chase()
    test_crc_addresses()
    test_branchy()
    test_probe()
    test_counter()
    test_timing()
    print('\n{} failed'.format(len(FAILED)) if FAILED else '\nall passed')
    sys.exit(1 if FAILED else 0)
