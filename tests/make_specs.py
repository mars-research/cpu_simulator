#!/usr/bin/env python3
"""Writes specs/<test>.json (the uiCA -memSpec for every test in measure.TESTS).

Instruction indices are positions in the kernel body (non-comment lines of kernels/<kernel>.s), which is
the order of uiCA's instruction list (a macro-fused branch keeps its own index).
The patterns mirror what the native harness does:
  chase / mchase<K>  one random cyclic permutation of the footprint's lines ("perm"); chain j of K starts
                     j * lines/K steps along it ("shift"); footprints <= 256 MiB are warmed, as natively
  rand               crc32c(i) & mask: modelled as "random" lines over 4 GiB
  pf_t0 / pf_t2      prefetch of f(i + D) and load of f(i): the same "random" pattern, shift D and 0
  mix_*              one independent "random" stream per memory instruction
"""
import json, re
from pathlib import Path
import measure

HERE = Path(__file__).resolve().parent


def body(kernel):
    lines = [l.split('#')[0].strip() for l in (HERE / 'kernels' / f'{kernel}.s').read_text().splitlines()]
    return [l for l in lines if l]


def spec(name):
    kernel, fp, _ = measure.TESTS[name]
    ins = body(kernel)
    region = {'buf': {'size': fp, 'warm': fp <= (256 << 20)}}
    acc = {}
    if kernel == 'chase':
        acc[0] = {'region': 'buf', 'pattern': 'perm', 'seed': 1}
    elif kernel.startswith('mchase'):
        k, n = int(kernel[6:]), fp // 64
        chase = [i for i, l in enumerate(ins) if re.fullmatch(r'mov rdx, \[rdx\]', l)]
        assert len(chase) == k
        for j, i in enumerate(chase):
            acc[i] = {'region': 'buf', 'pattern': 'perm', 'seed': 1, 'shift': j * (n // k)}
        # the L1-resident chain-pointer array: load [rax + 8j] / store [rax + 8j] (store-to-load forwarding
        # by concrete address between iterations)
        region['ptrs'] = {'size': 4096, 'warm': True}
        for i, l in enumerate(ins):
            m = re.fullmatch(r'mov (?:rdx, \[rax \+ (\d+)\]|\[rax \+ (\d+)\], rdx)', l)
            if m:
                acc[i] = {'region': 'ptrs', 'pattern': 'same', 'offset': int(m.group(1) or m.group(2))}
    elif kernel == 'rand':
        acc[next(i for i, l in enumerate(ins) if '[rdi + rdx]' in l)] = {'region': 'buf', 'pattern': 'random', 'seed': 1}
    elif kernel.startswith('mix_'):
        # one independent random stream per memory instruction (the native kernel hashes i with a seed per stream)
        mem = [i for i, l in enumerate(ins) if l.startswith(('add r8', 'prefetch'))]
        for j, i in enumerate(mem):
            acc[i] = {'region': 'buf', 'pattern': 'random', 'seed': 100 + j}
    elif kernel.startswith('pf_'):
        d = int(re.search(r'\[rcx \+ (\d+)\]', ins[0]).group(1))
        pf = next(i for i, l in enumerate(ins) if l.startswith('prefetch'))
        ld = next(i for i, l in enumerate(ins) if l.startswith('add r8'))
        acc[pf] = {'region': 'buf', 'pattern': 'random', 'seed': 1, 'shift': d}
        acc[ld] = {'region': 'buf', 'pattern': 'random', 'seed': 1}
    return {'config': 'EMR_d760', 'regions': region, 'accesses': {str(i): a for i, a in sorted(acc.items())}}


if __name__ == '__main__':
    (HERE / 'specs').mkdir(exist_ok=True)
    for name in measure.TESTS:
        (HERE / 'specs' / f'{name}.json').write_text(json.dumps(spec(name), indent=1) + '\n')
    print('wrote', len(measure.TESTS), 'specs')
