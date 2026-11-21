#!/bin/bash
# usage: run_roi.sh <inst> <nthreads_on_node0> <tag>
# Two perf processes share one ROI (enable 1 s after start barrier, disable when the first worker leaves its loop):
#   core:   perf -C <worker cpus>, 3 fixed + 4 programmable events (no multiplexing)
#   uncore: perf -a, HBM CAS rd/wr on all 32 boxes + CHA clock
D=$(dirname $(readlink -f $0)); B=$D/bw_roi; L=$D/logs; mkdir -p $L; inst=$1; n=$2; tag=$3
cpus=$(python3 -c "print(','.join(str(2*i) for i in range($n)))")
CORE=${CORE:-cycles,ref-cycles,instructions,offcore_requests_outstanding.data_rd,offcore_requests.data_rd,xq.full_cycles,l1d_pend_miss.fb_full}
HBM=$(for i in $(seq 0 31); do printf 'uncore_hbm_%d/event=0x05,umask=0xcf,name=hbm_rd/,uncore_hbm_%d/event=0x05,umask=0xf0,name=hbm_wr/,' $i $i; done)
UNC="${HBM}uncore_cha_0/event=0x01,name=clk_cha/"
F=$L/$tag; rm -f $F.{c1,a1,c2,a2}; mkfifo $F.c1 $F.a1 $F.c2 $F.a2
sudo -n perf stat -x, -D -1 --control fifo:$F.c2,$F.a2 -a --per-socket -e $UNC -o $F.unc.perf &
P2=$!; sleep 0.5
sudo -n env BW_PFONLY=${BW_PFONLY:-0} BW_HOTLINES=${BW_HOTLINES:-0} PERF_CTL=$F.c1:$F.c2 PERF_ACK=$F.a1:$F.a2 perf stat -x, -D -1 --control fifo:$F.c1,$F.a1 -C $cpus \
  -e $CORE -o $F.core.perf -- $B -m 1gb -pattern "n0a2t$n" -freq 2.7 -inst $inst -lookahead 64 -pad ${PAD:-0} -mode r > $F.out
sudo -n kill -INT $(pgrep -P $P2 perf || echo $P2) 2>/dev/null; wait $P2 2>/dev/null
rm -f $F.{c1,a1,c2,a2}
grep -E 'ROI|Time Taken' $F.out
