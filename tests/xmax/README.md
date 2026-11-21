# Xeon Max 9462 (HBM) ground truth for the memory model

Everything needed to keep developing `memsys.py` without the machine: the measured numbers, the raw logs they come
from, the native program and scripts that produced them, and the uiCA kernels that reproduce the same loop.

| file | what |
|---|---|
| `ground_truth.json` | measured per-test numbers (built from the raw logs by `make_truth.py`) |
| `compare.py` | simulates every test with uiCA and prints model vs measured; `--set key=value` overrides memsys parameters |
| `gen.sh` | builds a uiCA kernel + state: the prefetch-only loop exactly as gcc compiled it (`native/bw_roi_loops.s`) |
| `runcore.py` | uiCA with EMR core parameters overridden (`Param=value -- <uiCA args>`), for core experiments |
| `native/` | `bw_roi.c` (program), `run_roi.sh` (perf ROI runner), `pebs.sh`, `an_*.py` (analysis), `METHOD.md` (how the counters are measured and combined), `fbmodel.py` (the standalone FB queueing model used before uiCA), `bw_roi_loops.s` (the compiled loops and their alignment) |
| `../results/xmax/README.md` | the data behind each modelling decision (core calibration, overflow evidence, model sweeps) |
| `../results/xmax/logs/` | every raw run: `<tag>.out` (program output, ROI seconds), `<tag>.core.perf` / `<tag>.unc.perf` (perf stat CSV), `pebs_*.lat` (PEBS load-latency samples) |

Usage (from this directory, after `../../setup.sh`-equivalent setup and `pip install unicorn` into `../../.venv`):

    python3 make_truth.py                                    # rebuild ground_truth.json from the logs (optional)
    ../../.venv/bin/python compare.py                        # current model vs measured (about 15 s)
    ../../.venv/bin/python compare.py --only mem --set nOverflow=12 fbArb=new

Tests in `ground_truth.json`:
- `core_only`: prefetch-only t0 loop over 256 L1-resident lines, pad 0-48 dependent `add` per line: front end /
  rename only (cycles per line, uop sources).
- `pfo_t0_misses`: the same loop over 1 GiB on HBM (every prefetch misses), pads 0 / 16 / 48: cycles per line,
  U and W (offcore occupancy), p (hwpf share), fb_full, xq_full, L1 fills per prefetch.

Log tags in `../results/xmax/logs` (all 1 thread on 1 core unless `8x1` = 8 cores; `pfo` = prefetch-only,
`hint` / `pfohint` = l2_rqsts.hwpf_miss / swpf_miss runs, `dld` = demand-load hit counts, `roi` = with demand
load, `_p<N>` = pad N, `_hot` = L1-resident; see `native/METHOD.md` for the counters of each kind of run).
