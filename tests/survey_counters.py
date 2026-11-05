#!/usr/bin/env python3
"""Survey of core PMU events for memory-related load-uop behaviour the memory model may not capture.

Reuses measure.py (machine check, harness, test table, perf --control fifos so only the timed loop is
counted). Each group = fixed cycles + instructions + <= 4 programmable events (4 per hyperthread on this
machine, HT on), so nothing multiplexes; a group that multiplexes or is rejected is split in halves and
retried (logged in "_notes").

    python3 survey_counters.py [--tests rand_dram ...] [--groups queues ...] [--ldlat]

Outputs results/counter_survey.json: {event: {test: {"per_iter": x, "per_cycle": y}}}, plus "_groups",
"_notes" and (with --ldlat) "_ldlat": PEBS load-latency samples (dispatch -> completion) per test.
Raw events (cmask thresholds) are given as perf strings with name=...; see EVENTS below.
"""
import argparse, json, os, statistics, subprocess, sys, tempfile, collections
from pathlib import Path

import measure

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "counter_survey.json"

TESTS = ["chase_dram", "rand_dram", "pf_t0_dram", "pf_t2_dram", "mix_t2x2_dram", "mix_ld_t2x1_dram",
         "mix_ld_t2x2_dram", "mix_t0_t2x1_dram", "mix_ld_t0x1_dram", "mchase16_dram", "mchase32_dram",
         "mchase48_dram"]


def raw(name, ev, um, cmask=0, edge=0):
    s = f"cpu/event={ev:#x},umask={um:#x}" + (f",cmask={cmask}" if cmask else "") + (",edge=1" if edge else "")
    return name, s + f",name={name}/"


def osr(c):   # cycles with >= c outstanding L2-miss data reads (OFFCORE_REQUESTS_OUTSTANDING.DATA_RD, cmask)
    return raw(f"osr_data_rd_ge{c}", 0x20, 0x08, c)


def osr_dem(c):
    return raw(f"osr_demand_rd_ge{c}", 0x20, 0x01, c)


def fbp(c):   # cycles with >= c demand-held FBs (L1D_PEND_MISS.PENDING, cmask)
    return raw(f"l1d_pend_ge{c}", 0x48, 0x01, c)


# group -> list of (name, perf spec). Named events: spec == name.
def E(*names):
    return [(n, n) for n in names]


GROUPS = {
    "ld_dispatch": E("uops_dispatched.port_2_3_10", "mem_inst_retired.all_loads", "mem_load_completed.l1_miss_any",
                     "load_hit_prefetch.swpf"),
    "ld_src":      E("mem_load_retired.l1_hit", "mem_load_retired.l2_hit", "mem_load_retired.l3_hit",
                     "mem_load_retired.fb_hit"),
    "ld_src2":     E("mem_load_retired.l1_miss", "mem_load_retired.l2_miss", "mem_load_retired.l3_miss",
                     "mem_load_l3_miss_retired.local_dram"),
    "l2_rd":       E("l2_rqsts.demand_data_rd_hit", "l2_rqsts.demand_data_rd_miss", "l2_rqsts.swpf_hit",
                     "l2_rqsts.swpf_miss"),
    "l2_all":      E("l2_rqsts.references", "l2_rqsts.miss", "l2_lines_in.all", "l2_lines_out.silent"),
    "l1d_offcore": E("l1d.replacement", "l2_lines_out.non_silent", "offcore_requests.all_requests",
                     "offcore_requests.demand_data_rd"),
    "offcore_rd":  E("offcore_requests.data_rd", "offcore_requests.l3_miss_demand_data_rd",
                     "sw_prefetch_access.t0", "sw_prefetch_access.t1_t2"),
    "queues":      E("xq.full_cycles", "l1d_pend_miss.l2_stalls", "l1d_pend_miss.fb_full_periods",
                     "l1d_pend_miss.fb_full"),
    "offcore_occ": E("offcore_requests_outstanding.data_rd", "offcore_requests_outstanding.demand_data_rd",
                     "offcore_requests_outstanding.cycles_with_data_rd",
                     "offcore_requests_outstanding.l3_miss_demand_data_rd"),
    "osr_hist":    [osr(16), osr(24), osr(32), osr(40)],
    "osr_hist2":   [osr(44), osr(48), osr_dem(16), osr_dem(24)],
    "fb_hist":     [fbp(4), fbp(8), fbp(12), fbp(16)],
    "fb_occ":      E("l1d_pend_miss.pending", "l1d_pend_miss.pending_cycles") + [osr(8), osr_dem(8)],
    "mem_act":     E("memory_activity.stalls_l1d_miss", "memory_activity.stalls_l2_miss",
                     "memory_activity.stalls_l3_miss", "memory_activity.cycles_l1d_miss"),
    "cyc_act":     E("cycle_activity.cycles_l2_miss", "cycle_activity.cycles_mem_any",
                     "cycle_activity.stalls_total", "exe_activity.bound_on_loads"),
    "exe":         E("exe_activity.exe_bound_0_ports", "exe_activity.1_ports_util", "exe_activity.bound_on_stores",
                     "rs.empty"),
    "ld_blocks":   E("ld_blocks.store_forward", "ld_blocks.address_alias", "ld_blocks.no_sr",
                     "machine_clears.memory_ordering"),
    "tlb":         E("dtlb_load_misses.walk_active", "dtlb_load_misses.stlb_hit", "dtlb_load_misses.walk_completed",
                     "mem_inst_retired.stlb_miss_loads"),
    "uops":        E("uops_issued.any", "uops_executed.thread", "int_misc.recovery_cycles",
                     "resource_stalls.scoreboard"),
    "topdown":     E("topdown.slots_p", "topdown.backend_bound_slots", "topdown.memory_bound_slots",
                     "topdown.bad_spec_slots"),
    "l2_hwpf":     E("l2_rqsts.hwpf_miss", "l2_rqsts.all_hwpf", "l1d.hwpf_miss", "l2_rqsts.all_demand_miss"),
    # OCR events share 2 OFFCORE_RSP MSRs: at most 2 per group
    "ocr":         E("ocr.hwpf_l2.any_response", "ocr.hwpf_l1d.any_response", "l2_rqsts.all_demand_data_rd",
                     "l2_rqsts.all_rfo"),
    "ocr2":        E("ocr.demand_data_rd.any_response", "ocr.reads_to_core.any_response", "l2_lines_out.useless_hwpf",
                     "sw_prefetch_access.prefetchw"),
    "misc":        E("resource_stalls.sb", "mem_uop_retired.any", "int_misc.uop_dropping")
                   + [raw("sw_prefetch_access.any", 0x40, 0x0f)],
}
FIXED = measure.FIXED   # cycles, instructions


def counted(name, evs, reps=1):
    """Like measure.counted, for (name, spec) pairs (raw events) -> ({name: per-iter}, problem or None)."""
    with tempfile.TemporaryDirectory() as d:
        ctl, ack = f"{d}/ctl", f"{d}/ack"
        os.mkfifo(ctl); os.mkfifo(ack)
        specs = [s for _, s in [(f, f) for f in FIXED] + evs]
        cmd = (["sudo", "perf", "stat", "-x,", "-D", "-1", "--control", f"fifo:{ctl},{ack}", "-e", ",".join(specs),
                "--", "env", f"PERF_CTL={ctl}", f"PERF_ACK={ack}"] + measure.PIN + measure.harness_args(name, reps))
        p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        return None, f"perf failed: {p.stderr.strip()[-300:]}"
    iters = measure.TESTS[name][2] * reps
    names = set(FIXED) | {n for n, _ in evs}
    res, prob = {}, None
    for line in p.stderr.splitlines():
        f = line.split(",")
        if len(f) > 4 and f[2] in names:
            if not f[0] or f[0].startswith("<"):
                prob = f"{f[2]}: {f[0]}"
                continue
            if float(f[4] or 100) <= 99:
                prob = f"multiplexed: {line}"
            res[f[2]] = float(f[0]) / iters
    missing = names - set(res)
    if missing and not prob:
        prob = f"missing {sorted(missing)}"
    return res, prob


def measure_group(test, gname, evs, notes):
    res, prob = counted(test, evs)
    if prob is None:
        return res
    notes.append(f"{test}/{gname} {[n for n, _ in evs]}: {prob}")
    if len(evs) == 1:
        return {k: v for k, v in (res or {}).items() if not prob.startswith("multiplexed")}
    h = len(evs) // 2
    out = {}
    for part in (evs[:h], evs[h:]):
        out.update(measure_group(test, gname + "/split", part, notes))
    return out


def ldlat(test, thresh=4, period=23):
    """PEBS load-latency samples (mem_trans_retired.load_latency_gt_<thresh>) over the timed loop ->
    {data source: [n, mean, p50, p90]} (latency = dispatch -> completion, includes FB/re-dispatch waits)."""
    with tempfile.TemporaryDirectory() as d:
        ctl, ack, data = f"{d}/ctl", f"{d}/ack", f"{d}/perf.data"
        os.mkfifo(ctl); os.mkfifo(ack)
        cmd = (["sudo", "perf", "record", "-q", "-D", "-1", "--control", f"fifo:{ctl},{ack}", "-W", "-d", "-c", str(period),
                "-o", data, "-e", f"{{cpu/mem-loads-aux/,cpu/mem-loads,ldlat={thresh}/upp}}",
                "--", "env", f"PERF_CTL={ctl}", f"PERF_ACK={ack}"] + measure.PIN + measure.harness_args(test, 1))
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            return {"error": p.stderr.strip()[-300:]}
        s = subprocess.run(["sudo", "perf", "script", "-i", data, "-F", "event,ip,weight,data_src"],
                           capture_output=True, text=True).stdout
    by = collections.defaultdict(list)
    for line in s.splitlines():
        f = line.split()
        if len(f) < 3 or "|" not in line:
            continue
        try:
            w, ip = int(f[-2]), f[-1]
        except ValueError:
            continue
        lvl = next((t.strip()[4:] for t in line.split("|") if t.strip().startswith("LVL")), "?")
        if ip.startswith("ffff"):
            continue                       # kernel (aux / interrupt) samples
        by[(ip, lvl)].append(w)
    out = {}
    for (ip, lvl), ws in sorted(by.items(), key=lambda kv: -len(kv[1])):
        ws.sort()
        out[f"{ip} {lvl}"] = [len(ws), round(statistics.mean(ws), 1), ws[len(ws) // 2], ws[int(len(ws) * 0.9)]]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tests", nargs="+", default=TESTS)
    ap.add_argument("--groups", nargs="+", default=list(GROUPS))
    ap.add_argument("--ldlat", action="store_true", help="also take PEBS load-latency samples")
    args = ap.parse_args()
    measure.check_machine()
    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    out["_groups"] = {g: [s for _, s in GROUPS[g]] for g in GROUPS}
    out.setdefault("_notes", [])
    for test in args.tests:
        for g in args.groups:
            res = measure_group(test, g, GROUPS[g], out["_notes"])
            cyc = res.get("cycles")
            for ev, v in res.items():
                key = ev if ev not in FIXED else f"{ev}"
                out.setdefault(key, {})[test] = {"per_iter": round(v, 5),
                                                 "per_cycle": round(v / cyc, 6) if cyc else None, "group": g}
            print(f"{test:18s} {g:12s} " + " ".join(f"{k}={v:.3f}" for k, v in res.items() if k not in FIXED),
                  flush=True)
            OUT.write_text(json.dumps(out, indent=1))
        if args.ldlat:
            out.setdefault("_ldlat", {})[test] = ldlat(test)
            print(test, "ldlat", json.dumps(out["_ldlat"][test])[:400], flush=True)
            OUT.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
