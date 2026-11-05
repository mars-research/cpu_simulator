#!/usr/bin/env python3
"""Writes states/<test>.json: the -state (initial registers + memory) of every test in measure.TESTS, set up
the way the native harness sets up the kernel (harness.c), so execution decides the addresses:
  chase       buf holds one random cyclic chain through all its lines; rax = a node, rsi = iterations
  mchase<K>   the same chain; ptrs (one L1-resident page) holds K chain pointers, n/K lines apart along it
  index       rdi = buf, rcx = 0, rsi = iterations (the kernel computes its own crc32c addresses)

Footprints <= 256 MiB start warm, as natively. The chain is execstate's "chase" layer (seed 1); chase starts at
its position 0, and mchase's K chains start n/K positions apart along it, as the harness does.
Iterations: enough for ~80k simulated cycles (at the measured speed), at least 200.
"""
import json, math, sys
from pathlib import Path
import measure

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from execstate import Permutation


def state(name, cycles=80_000):
    kernel, fp, _ = measure.TESTS[name]
    gt = json.loads((HERE / 'ground_truth.json').read_text()).get(name, {}).get('cycles_per_iter', 20)
    iters = max(200, math.ceil(cycles / gt))
    warm = fp <= (256 << 20)
    st = {'config': 'EMR_d760', 'registers': {}, 'memory': {}}
    if kernel == 'chase' or kernel.startswith('mchase'):
        n = fp // 64
        perm = Permutation(n.bit_length() - 1, 1)
        st['memory']['buf'] = {'size': fp, 'warm': warm, 'init': [{'kind': 'chase', 'seed': 1}]}
        if kernel == 'chase':
            st['registers'] = {'rax': f'buf+{perm.fwd(0) * 64:#x}', 'rsi': iters}
        else:
            k = int(kernel[6:])
            starts = [f'buf+{perm.fwd(j * (n // k)) * 64:#x}' for j in range(k)]
            st['memory']['ptrs'] = {'size': 4096, 'warm': True, 'init': [{'kind': 'values', 'data': starts}]}
            st['registers'] = {'rax': 'ptrs', 'rsi': iters}
    else:
        st['memory']['buf'] = {'size': fp, 'warm': warm}
        st['registers'] = {'rdi': 'buf', 'rcx': 0, 'rax': 0, 'r8': 0, 'rsi': iters}
    return st


if __name__ == '__main__':
    (HERE / 'states').mkdir(exist_ok=True)
    for name in measure.TESTS:
        (HERE / 'states' / f'{name}.json').write_text(json.dumps(state(name), indent=1) + '\n')
    print('wrote', len(measure.TESTS), 'states')
