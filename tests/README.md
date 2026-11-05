# uiCA memory-model tests

Short loop kernels whose memory accesses miss the caches, measured natively on the CloudLab d760
(Xeon Gold 6548Y+, Emerald Rapids) and simulated with uiCA + the memory model (`../memsys.py`,
`-memSpec`). The same `kernels/<k>.s` loop body is used for both.

## Workflow

    ./build.sh                      # build/harness (native) and build/<k>.o (uiCA input)
    python3 measure.py              # native ground truth -> ground_truth.json   (needs the machine set up, below)
    python3 make_specs.py           # specs/<test>.json, the -memSpec of every test
    ../.venv/bin/python run_uica.py [--set nFB=16 nL2Q=25 ...] [--tag name]   # predicted vs measured -> results/<tag>.json

One test by hand:

    ../.venv/bin/python ../uiCA.py build/pf_t0.o -arch EMR -memSpec specs/pf_t0_dram.json

Machine state for `measure.py` (it refuses to run otherwise): `scripts/setup.sh` (core 2.5 GHz pinned, no
turbo, no C-states), uncore 2.5 GHz, hardware prefetchers off
(`sudo env PATH="$PATH" scripts/prefetch_control_hbm.sh off` = MSR 0x1a4 0x2f), >= 4 free 1 GiB pages on
node 0. Runs are single-threaded on cpu 0 with memory on node 0; buffers use 1 GiB pages (no TLB misses).

## Tests

| test | kernel | footprint | what it exercises |
|---|---|---|---|
| chase_l1 / _l2 / _llc / _dram | `chase.s`: `mov rax, [rax]` | 16 KiB / 1 MiB / 32 MiB / 4 GiB | load-to-use latency of each level (calibration) |
| rand_dram | `rand.s`: load of line `crc32c(i)` | 4 GiB | independent misses: memory-level parallelism |
| pf_t0_dram, pf_t2_dram | `pf_t*.s`: `prefetcht0/t2 [f(i+D)]`, load `[f(i)]` | 4 GiB | the DRAMHiT find pattern (prefetch at enqueue, load at pop) |
| mchase{8..48}_dram | `mchase<K>.s`: K interleaved chases | 4 GiB | MLP limit sweep: time per access vs K outstanding misses |

`ground_truth.json` has cycles per iteration (median of 5 reps; TSC = core clock) and, per iteration,
core counters (L1/L2/L3 misses, fill-buffer hits and occupancy, fb_full cycles, offcore occupancy,
software-prefetch counts, stalls), each counter group in its own run, counting only the timed loop.

## Measured (d760, 2026-11-04)

| test | cycles/iter | notes |
|---|---|---|
| chase_l1 | 5.03 | L1 load-to-use 5 (no 4-cycle fast pointer chasing) |
| chase_l2 | 16.1 | |
| chase_llc | 72.7 | |
| chase_dram | 316 | 126 ns |
| rand_dram | 15.2 | |
| pf_t0_dram | 13.2 | |
| pf_t2_dram | 11.4 | |
| mchase8 .. 48 | 40.4 .. 12.8 per access | plateau ~24.7 misses in flight |

What the counters say about the miss-handling limit (see `memsys.py`, `nFB` / `nL2Q` / `fbFullL2Hint`):
demand L1-miss (fill buffer) occupancy saturates at ~14 with fb_full ~85 % of cycles (16 FBs), while
outstanding L2 misses keep growing to ~24 and the per-miss FB hold time drops from 310 to 176 cycles.
So a miss that finds the FBs full still sends its request out; the per-core limit is ~25 outstanding
L2 misses, not the 16 FBs.

## uiCA changes made for this (besides the memory model)

- `EMR` microarchitecture (`microArchConfigs.py`, instruction data from uops.info's EMR measurements,
  `convertXML.py`, the 12-port scheduler branch in `uiCA.py`).
- Fix (`instructions.py`): from ICL on, uops.info reports latency 0 from a store's data register to
  memory; uiCA treated that like a zero idiom and dropped the register, so values passed through memory
  (store -> load) had no dependency (a store/load chain simulated at 1 cycle/iter on ICL..EMR, 9-10 on SKL).
- Fix (`uiCA.py` renamer): on EMR, uops.info reports 0 uops for add/sub/inc/dec reg,imm and lea (executed at
  rename); uiCA gave their result no dependency at all. They now alias their input (latency 0).

## Model vs measured (EMR, `run_uica.py`)

| model | mean abs error (15 tests) | worst |
|---|---|---|
| 16 FBs held for the whole miss, 48 L2 misses (`--set nL2Q=48 fbFullL2Hint=0`) | 24.5 % | pf_t2 +82 %, mchase48 +55 % |
| **default**: 16 FBs, 25 L2 misses, full-FB misses go out without an FB | **5.3 %** | rand_dram −18 %, mchase24 −14 % |

Open: rand_dram runs at 15.2 cycles/iter natively but the model (and mchase48, 12.8/access) say ~12.5;
the measured knee of the MLP curve is gradual (latency rises from 316 to ~370 cycles by K = 24) while
the model's is sharp; LLC latency under-predicted by 5 % (non-inclusive LLC fill policy is simplified).
