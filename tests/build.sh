#!/usr/bin/env bash
# Builds, from the same kernels/<k>.s loop bodies:
#   build/harness      native driver (harness.c + one wrapped function k_<k> per kernel)
#   build/<k>.o        the loop body alone, the uiCA input:  ../uiCA.py build/<k>.o -arch ...
# Kernel register contract: rdi = buffer, rsi = loop limit (chase: iteration count), rax = rcx = start
# (chase: first node; mchase<K>: array of K chain pointers; index kernels: first index), r8 = accumulator; the body ends with a branch to 1b.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build
WRAP=build/kernels.s
echo ".intel_syntax noprefix" > $WRAP
TAB=build/kernel_table.h
: > $TAB.decl; : > $TAB.ent
for f in kernels/*.s; do
  k=$(basename "$f" .s)
  cat >> $WRAP <<W
.text
.globl k_$k
.p2align 6
k_$k:
    mov rax, rdx
    mov rcx, rdx
    xor r8d, r8d
    .p2align 6
1:
    .include "$f"
    lea rax, [rax + r8]
    ret
W
  printf '.intel_syntax noprefix\n.text\n1:\n.include "%s"\n' "$f" > build/$k.uica.s
  as build/$k.uica.s -o build/$k.o
  echo "uint64_t k_$k(char *, uint64_t, uint64_t);" >> $TAB.decl
  echo "  {\"$k\", k_$k}," >> $TAB.ent
done
{ cat $TAB.decl; echo "static const struct { const char *name; kernel_fn fn; } KERNELS[] = {"; cat $TAB.ent; echo "  {0, 0}};"; } > $TAB
rm $TAB.decl $TAB.ent
gcc -O2 -Ibuild -o build/harness harness.c $WRAP
echo "built: build/harness $(ls build/*.o | tr '\n' ' ')"
