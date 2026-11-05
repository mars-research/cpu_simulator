# uiCA memory-model tests

Short loop kernels whose memory accesses miss the caches, measured natively on the CloudLab d760
(Xeon Gold 6548Y+, Emerald Rapids) and simulated execution-driven with uiCA + the memory model
(`../execstate.py`, `../memsys.py`, `-state`). The same `kernels/<k>.s` loop body is used for both.

## Workflow

    ../.venv/bin/pip install unicorn  # once: the emulator behind -state
    ./build.sh                        # build/harness (native) and build/<k>.o (uiCA input)
    python3 measure.py                # native ground truth -> ground_truth.json   (needs the machine set up, below)
    ../.venv/bin/python make_states.py   # states/<test>.json: initial registers + memory, set up as the harness does
    ../.venv/bin/python run_uica.py [--set nFB=16 nL2Q=48 ...] [--tag name]   # predicted vs measured -> results/<tag>.json
    ../.venv/bin/python test_execstate.py   # functional checks (registers, paths, addresses)

One test by hand:

    ../.venv/bin/python ../uiCA.py build/chase.o -arch EMR -state states/chase_l2.json
    ../.venv/bin/python ../uiCA.py build/exec_probe.o -arch EMR -state states/exec_probe.json   # after test_execstate.py

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

What the counters say about the miss-handling limit (see `memsys.py`, `nFB` / `nL2Q` / `fbBusyBypass`):
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

## Model vs measured (EMR, `run_uica.py`, 20 tests)

| model | mean abs error | worst |
|---|---|---|
| **default**: 16 FBs held for a per-request-type service time (`fbServiceTime` ld 244 / t0 172 / t2 122), 48 L2 misses, a miss waits in the scheduler while all FBs are busy | **6.8 %** | pf_t2 −24 %, mchase48 +19 % |
| default + fill-buffer busy bypass (`--set fbBusyBypass=1 nBypassQ=28`; off by default; measured with the former address-pattern input) | 14.9 % | |

Open: prefetch then load of the same line (pf_t2 −24 %, pf_t0 −16 %); the measured knee of the MLP curve
is gradual (mchase20 −9 %, mchase48 +19 %); LLC latency under-predicted by 3-5 %.
