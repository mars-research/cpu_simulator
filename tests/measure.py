#!/usr/bin/env python3
"""Ground truth for the uiCA memory tests on this machine -> ground_truth.json.

One thread on cpu 0, memory bound to node 0 (numactl --membind=0 taskset -c 0), the machine in the
state scripts/setup.sh leaves it in (core and uncore 2.5 GHz, no turbo, no C-states, MSR 0x1a4 = 0x2f:
hardware prefetchers off) and >= 4 free 1 GiB pages on node 0.

Per test: `--reps` timed reps (cycles per iteration = median), then one run per counter group
(4 programmable + fixed counters, so nothing is multiplexed), counting only the timed loop via
perf's --control fifos. Counter values are reported per iteration.

    python3 measure.py [--tests chase_l1 ...] [--reps 5]
"""
import argparse, json, os, statistics, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "build" / "harness"
PIN = ["numactl", "--membind=0", "taskset", "-c", "0"]

# name: (kernel, footprint bytes, iterations per rep)
TESTS = {
    "chase_l1":   ("chase", 16 << 10, 20_000_000),
    "chase_l2":   ("chase", 1 << 20, 20_000_000),
    "chase_llc":  ("chase", 32 << 20, 4_000_000),
    "chase_dram": ("chase", 4 << 30, 1_000_000),
    "rand_dram":  ("rand", 4 << 30, 8_000_000),
    "pf_t0_dram": ("pf_t0", 4 << 30, 8_000_000),
    "pf_t2_dram": ("pf_t2", 4 << 30, 8_000_000),
    # request-type mixes: independent random lines per stream (fill-buffer service time per type)
    **{f"{k}_dram": (k, 4 << 30, 8_000_000) for k in ("mix_ld_t2x1", "mix_ld_t2x2", "mix_t0_t2x1", "mix_ld_t0x1", "mix_t2x2")},
    # memory-level-parallelism probe: K interleaved independent chases (K accesses per iteration)
    **{f"mchase{k}_dram": (f"mchase{k}", 4 << 30, 1_600_000 // k) for k in (8, 12, 16, 20, 24, 32, 40, 48)},
}
FIXED = ["cycles", "instructions"]
GROUPS = {
    "loads":   ["mem_load_retired.l1_miss", "mem_load_retired.fb_hit", "mem_load_retired.l2_miss",
                "mem_load_retired.l3_miss"],
    "fb":      ["l1d_pend_miss.fb_full", "l1d_pend_miss.pending", "l1d_pend_miss.pending_cycles",
                "offcore_requests_outstanding.data_rd"],
    "offcore": ["offcore_requests.data_rd", "l2_rqsts.swpf_miss", "sw_prefetch_access.t0",
                "sw_prefetch_access.t1_t2"],
    "stall":   ["cycle_activity.stalls_total", "cycle_activity.stalls_l1d_miss", "resource_stalls.sb",
                "uops_issued.any"],
}


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def check_machine():
    bad = []
    if set(sh("cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_m*_freq").split()) != {"2500000"}:
        bad.append("cpu0 not pinned to 2.5 GHz")
    if sh("cat /sys/devices/system/cpu/intel_pstate/no_turbo") != "1":
        bad.append("turbo on")
    if sh('sudo env PATH="$PATH" rdmsr -p 0 0x1a4') != "2f":
        bad.append("hardware prefetchers not off (MSR 0x1a4 != 0x2f)")
    if int(sh("cat /sys/devices/system/node/node0/hugepages/hugepages-1048576kB/free_hugepages") or 0) < 4:
        bad.append("< 4 free 1 GiB pages on node 0")
    if bad:
        sys.exit("machine not set up: " + "; ".join(bad))


def harness_args(name, reps):
    k, fp, it = TESTS[name]
    return [str(HARNESS), k, str(fp), str(it), str(reps)]


def timed(name, reps):
    out = subprocess.run(PIN + harness_args(name, reps), capture_output=True, text=True, check=True).stdout
    return [float(l.split()[3]) for l in out.splitlines() if l.startswith("rep ")]


def counted(name, events, reps=2):
    """perf stat over the timed reps only -> {event: count per iteration}."""
    with tempfile.TemporaryDirectory() as d:
        ctl, ack = f"{d}/ctl", f"{d}/ack"
        os.mkfifo(ctl); os.mkfifo(ack)
        cmd = (["sudo", "perf", "stat", "-x,", "-D", "-1", "--control", f"fifo:{ctl},{ack}", "-e", ",".join(events),
                "--", "env", f"PERF_CTL={ctl}", f"PERF_ACK={ack}"] + PIN + harness_args(name, reps))
        p = subprocess.run(cmd, capture_output=True, text=True, check=True)
    iters = TESTS[name][2] * reps
    res = {}
    for line in p.stderr.splitlines():
        f = line.split(",")
        if len(f) > 4 and f[2] in events:
            assert float(f[4] or 100) > 99, f"multiplexed: {line}"
            res[f[2]] = float(f[0]) / iters
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tests", nargs="+", default=list(TESTS))
    ap.add_argument("--reps", type=int, default=5)
    args = ap.parse_args()
    check_machine()
    outp = HERE / "ground_truth.json"
    gt = json.loads(outp.read_text()) if outp.exists() else {}
    gt["_machine"] = {"cpu": sh("lscpu | grep 'Model name' | cut -d: -f2").strip(), "core_ghz": 2.5,
                      "placement": "1 thread on cpu 0, memory node 0, 1 GiB pages, hw prefetchers off"}
    for name in args.tests:
        cpi = timed(name, args.reps)
        ctr = {}
        for g in GROUPS.values():
            ctr.update(counted(name, FIXED + g))
        k, fp, it = TESTS[name]
        gt[name] = {"kernel": k, "footprint": fp, "iters_per_rep": it,
                    "cycles_per_iter": round(statistics.median(cpi), 3), "cycles_per_iter_reps": cpi,
                    "per_iter": {e: round(v, 4) for e, v in sorted(ctr.items())}}
        print(f"{name:12s} {gt[name]['cycles_per_iter']:8.2f} cyc/iter  reps {cpi}  "
              f"l1_miss {ctr['mem_load_retired.l1_miss']:.3f} fb_hit {ctr['mem_load_retired.fb_hit']:.3f} "
              f"fb_full {ctr['l1d_pend_miss.fb_full']:.2f}", flush=True)
        outp.write_text(json.dumps(gt, indent=1))


if __name__ == "__main__":
    main()
