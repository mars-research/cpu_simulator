#!/bin/bash
# Build uiCA inputs for the bw_roi prefetch-only loop: line = crc32(seed, i + 64) & MASK (default 2^24 - 1: 1 GiB buffer),
# prefetch{t0,t1} of it, then PAD dependent `add r11, 1` (one chain, as DPAD8 in bw_roi.c).
# rdi = buffer, rcx = i, rsi = iterations.  Usage: [CFG='"key": value, ...'] [PFO_MASK=0xff PFO_SIZE=16384 PFO_WARM=true] gen.sh <t0|t1> <pad> [iterations=6000] [preset=XMAX_hbm]
set -e; cd "$(dirname "$0")"; mkdir -p build states
h=$1; pad=$2; n=${3:-6000}; preset=${4:-XMAX_hbm}; k=pfo_${h}_p${pad}${TAG:+_$TAG}
# same instructions and order as gcc's loop in bw_roi (PFO_LOOP_T0/T1): r15 = crc seed, r12 = line mask
{ echo ".intel_syntax noprefix"; echo ".text"; echo "1:"
  echo "    mov rax, r15"; echo "    crc32 rax, rcx"; echo "    and rax, r12"; echo "    shl rax, 6"
  echo "    prefetch$h [rdi + rax]"
  for ((j=0;j<pad;j++)); do echo "    add r11, 1"; done
  echo "    inc rcx"; echo "    cmp rcx, rsi"; echo "    jne 1b"; } > build/$k.s
as build/$k.s -o build/$k.o
cat > states/$k.json <<J
{"config": {"preset": "$preset"${CFG:+, $CFG}}, "registers": {"rdi": "buf", "rcx": 64, "rsi": $((n + 64)), "r11": 0, "r15": 3735928559, "r12": $((${PFO_MASK:-0xffffff}))},
 "memory": {"buf": {"size": ${PFO_SIZE:-1073741824}, "warm": ${PFO_WARM:-false}}}}
J
echo build/$k.o states/$k.json
