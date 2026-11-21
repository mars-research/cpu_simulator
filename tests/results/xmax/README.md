# Xeon Max 9462 (Sapphire Rapids + HBM): data behind the core and memory-model decisions

Machine: Xeon Max 9462 (Golden Cove cores), 1 thread on 1 core (CPU 0), data on HBM (NUMA node 2), hardware
prefetchers off (MSR 0x1a4 = 0x2f), turbo off: core 2.69-2.70 GHz (cycles / ref-cycles), uncore 2.5 GHz.
Program: DRAMHiT `scripts/eurosys_2026/optimize_hbm/lfb_rules/bw_roi.c` (built `-O3 -march=native -DRANDOM`),
prefetch-only loop (`BW_PFONLY=1`): per iteration `prefetcht0` of line crc32(seed, i + 64) & mask, then PAD
dependent `add $1, reg`. Counters with `perf stat` over a region of interest (perf `--control fifo`, enabled 1 s
after the start barrier, disabled when the worker leaves its loop); every event counted 100 % of the time.
Raw logs: `logs/<tag>.{out,core.perf,unc.perf}` (copied from `lfb_rules/logs`). Collected 2026-11-19/20.

The compiled loop (gcc), pad 0, 8 instructions / 7 fused uops:

    mov rax, r15; crc32 rax, rdx; and rax, rsi; shl rax, 6; inc rdx; prefetcht0 [rdi + rax]; cmp rdx, rcx; jne
    (pad N: the N adds follow the prefetch; loop start offsets in a 64-byte line: pad 0/8: 48, 16: 16, 24: 8,
    32: 32, 48: 24 -> uiCA -alignmentOffset)

The uiCA kernels with the same instructions and order: `lfb_rules/uica/gen.sh`.

## 1. Core (front end / rename), no memory misses

`BW_HOTLINES=256`: the index is masked to 256 lines (16 KiB, L1-resident); same iteration count. Logs
`fe_t0_p<pad>_hot` (and `core_t0_p<pad>_hot`). Lines = instructions / (8 + pad).

| pad | cycles/line | uops/line (uops_issued.any) | idq.dsb_uops | idq.mite_uops | lsd.uops | uops issued / cycle | L1 refills / line | L2 misses / line |
|---|---|---|---|---|---|---|---|---|
| 0 | 2.18 | 7.0 | 7.0 | 0 | 0 | 3.22 | 0.0001 | 0 |
| 8 | 3.59 | 15.0 | 15.0 | 0 | 0 | 4.19 | | |
| 16 | 5.36 | 23.0 | 23.0 | 0 | 0 | 4.30 | 0.0001 | 0 |
| 24 | 7.09 | 31.0 | 31.0 | 0 | 0 | 4.38 | | |
| 32 | 8.59 | 39.1 | 39.0 | 0 | 0 | 4.55 | | |
| 48 | 12.07 | 55.1 | 55.0 | 0 | 0 | 4.57 | 0.0002 | 0 |

Facts used:
- every uop comes from the DSB; the LSD is never used, the legacy decoders deliver nothing -> `LSDEnabled = False`;
- the 7-uop loop takes 2 cycles: the DSB delivers at most 6 uops per cycle and nothing after a taken branch in the
  same cycle -> `DSBWidth = 6`, `DSBTakenBranchEndsCycle = True`;
- each extra dependent `add reg, 1` costs 0.206 cycles (about 4.85 per cycle; uops.info: 0 uops, executed at
  rename) -> `renameExecWidth = 4` (best of 4 / 5 / fractional 4.6-4.85).

uiCA (`-arch EMR -alignmentOffset <real> -state`, 256 warm lines), cycles/line:

| configuration | 0 | 8 | 16 | 24 | 32 | 48 | mean abs error |
|---|---|---|---|---|---|---|---|
| real | 2.18 | 3.59 | 5.36 | 7.09 | 8.59 | 12.07 | |
| before (LSD on, DSB 8 wide) | 1.25 | | 4.00 | | | 10.00 | ~30 % |
| LSD off, DSB 8 wide | 1.17 | 2.50 | 3.83 | 5.17 | 6.50 | 9.17 | 32 % |
| LSD off, DSB 6 wide, taken branch ends the DSB cycle | 2.00 | 3.00 | 4.00 | 6.00 | 7.00 | 10.00 | 16 % |
| + renameExecWidth 5 | 2.00 | 3.00 | 4.20 | 6.00 | 7.40 | 10.60 | 13 % |
| + renameExecWidth 4.6 / 4.85 (token bucket) | 2.00 | 3.00 | 4.40 | 6.20 | 8.00 | 11.50 | 11 % |
| **+ renameExecWidth 4 (adopted)** | 2.00 | 3.00 | 5.00 | 7.00 | 9.00 | 13.00 | **7.5 %** |

Not explained: a per-iteration overhead of ~0.2-0.6 cycles in the real loop (pad 8: 3.59 vs 3.00).
Issue width alone does not fit (issue width 5, LSD on: 1.50 / 5.00 / 11.00 for pads 0 / 16 / 48).

## 2. Memory: prefetch-only t0 with misses (1 GiB buffer on HBM)

Logs `pfo_t0_1x1`, `pfohint_t0_1x1` (pad 0), `pfo_t0_p16_1x1`, `pfohint_t0_p16_1x1` (pad 16), `pend_t0_p48_1x1`,
`fbfull_t0_p48_1x1` (pad 48; two runs, averaged). Lines = offcore_requests.data_rd.

| pad | cycles/line | U = outstanding / cycles | W = outstanding / requests (cycles) | p = hwpf_miss / prefetches | swpf_miss / prefetches | fb_full / cycles | xq.full / cycles | l1d.replacement / prefetch |
|---|---|---|---|---|---|---|---|---|
| 0 | 11.1 | 29.3 | 325 | 0.826 | 0.174 | 0.93 | 0.15 | 1.001 |
| 16 | 16.9 | 19.1 | 323 | 0.730 | 0.270 | 0.88 | 0.00 | 1.001 |
| 48 | 22.2 | 16.7 | 371 | 0.53 | (0.47) | 0.65 | - | - |

Facts used: with hardware prefetchers off, l2_rqsts.hwpf_miss + l2_rqsts.swpf_miss = 1.000 per prefetcht0, and
every prefetcht0 fills L1 (l1d.replacement 1.001): a prefetcht0 that finds the FBs full still goes to memory (as
an L2 "hardware prefetch") and later fills L1, so it must get an FB later -> the FB overflow rule of `memsys.py`.
`l1d_pend_miss.pending` reads 0.008 per cycle on the prefetch-only loop: it does not count prefetch FBs.

Model (`memsys.py` preset `XMAX_hbm`, adopted core), prefetch-only t0, 3000 lines, pads 0 / 16 / 48:

| nOverflow, fbArb | cycles/line | U | p | fb_wait (cf. fb_full) | mean error (cycles/line and U) |
|---|---|---|---|---|---|
| measured | 11.1 / 16.9 / 22.2 | 29.3 / 19.1 / 16.7 | .83 / .73 / .53 | .93 / .88 / .65 | |
| 8, old | 14.6 / 15.0 / 20.5 | 22.1 / 21.4 / 15.8 | .33 / .34 / .47 | 1.00 / 1.00 / .95 | 15 % |
| 12, old | 12.8 / 14.0 / 18.8 | 25.6 / 23.0 / 17.3 | .43 / .47 / .63 | 1.00 / 1.00 / 1.00 | 14 % |
| 16, old | 11.4 / 13.3 / 17.5 | 28.7 / 24.6 / 18.6 | .49 / .58 / .76 | 1.00 / 1.00 / 1.00 | 15 % |
| 16, new | 11.3 / 13.8 / 17.2 | 28.6 / 23.4 / 18.9 | .49 / .50 / .74 | 1.00 / 1.00 / 1.00 | 14 % |
| 20, old | 10.5 / 12.3 / 16.1 | 31.1 / 26.4 / 20.3 | .53 / .67 / .87 | 1.00 / 1.00 / 1.00 | 21 % |
| unlimited, old | 8.9 / 9.3 / 14.2 | 85.2 / 34.9 / 22.9 | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 | 69 % |

Open: the model's FBs are busy every cycle at every pad and its p rises with pad; measured fb_full and p fall with
pad, and the real loop slows more with pad than the model (pad 48: 22.2 vs 17.5 cycles/line).
