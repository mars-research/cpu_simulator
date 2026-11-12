// steptrace: native execution trace of one function, for uiCA -state with a "trace" (tracestate.py).
//
// Runs a program under ptrace, waits until any thread enters [lo, lo+size), then single-steps that thread
// on the real CPU (so every ISA extension the host has works, AVX-512 included) and writes, for every
// instruction executed inside the range, RIP and the 16 GPRs as they are *before* the instruction runs.
// Code outside the range (callers, callees) is stepped through but not recorded. Other threads run
// untraced at full speed. When done, the program is killed.
//
//   cc -O2 -o steptrace steptrace.c
//   steptrace -o out.trc -lo 0x468d80 -size 0xaef [-pie] [-n 1000000] [-calls 0] [-delay-ms 0] -- cmd args...
//
//   -lo/-size  function range (link-time address; with -pie relative to the executable's load base)
//   -n         stop after this many recorded instructions        -calls  stop after this many returns (0: no limit)
//   -delay-ms  arm the entry breakpoint only after this delay (skip the program's first calls)
//   -after-ms  let the first entry pass, run untraced for this long, then trace the next entry (e.g. the middle
//              of a find phase instead of its first lookups)
//
// Output: header {char magic[8] = "STEPTRC1"; u64 lo (runtime); u64 size; u64 n} followed by n records of
// 17 u64: rip, rax, rbx, rcx, rdx, rsi, rdi, rbp, rsp, r8 .. r15.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/types.h>
#include <sys/user.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static void die(const char *m) { perror(m); exit(1); }

static uint64_t now_ms(void) {
  struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
  return (uint64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static int memfd;
static void mem_rw(int wr, uint64_t addr, void *buf, size_t n) {
  ssize_t r = wr ? pwrite(memfd, buf, n, (off_t)addr) : pread(memfd, buf, n, (off_t)addr);
  if (r != (ssize_t)n) die(wr ? "write /proc/pid/mem" : "read /proc/pid/mem");
}

static uint64_t load_base(pid_t pid) {
  char path[64], exe[4096], line[8192];
  snprintf(path, sizeof path, "/proc/%d/exe", pid);
  ssize_t k = readlink(path, exe, sizeof exe - 1); if (k < 0) die("readlink exe"); exe[k] = 0;
  snprintf(path, sizeof path, "/proc/%d/maps", pid);
  FILE *f = fopen(path, "r"); if (!f) die("maps");
  uint64_t base = 0;
  while (fgets(line, sizeof line, f)) {
    unsigned long s, e, off; char name[4096] = "";
    if (sscanf(line, "%lx-%lx %*s %lx %*s %*s %4095s", &s, &e, &off, name) >= 3 && off == 0 && !strcmp(name, exe)) {
      base = s; break;
    }
  }
  fclose(f);
  if (!base) { fprintf(stderr, "no mapping of %s at offset 0\n", exe); exit(1); }
  return base;
}

static void put_regs(uint64_t *o, const struct user_regs_struct *r) {
  o[0] = r->rip; o[1] = r->rax; o[2] = r->rbx; o[3] = r->rcx; o[4] = r->rdx; o[5] = r->rsi; o[6] = r->rdi;
  o[7] = r->rbp; o[8] = r->rsp; o[9] = r->r8; o[10] = r->r9; o[11] = r->r10; o[12] = r->r11; o[13] = r->r12;
  o[14] = r->r13; o[15] = r->r14; o[16] = r->r15;
}

int main(int argc, char **argv) {
  const char *out = NULL; uint64_t lo = 0, size = 0, maxn = 1000000, maxcalls = 0, delay = 0, after = 0; int pie = 0, i;
  for (i = 1; i < argc && strcmp(argv[i], "--"); i++) {
    if (!strcmp(argv[i], "-o")) out = argv[++i];
    else if (!strcmp(argv[i], "-lo")) lo = strtoull(argv[++i], 0, 0);
    else if (!strcmp(argv[i], "-size")) size = strtoull(argv[++i], 0, 0);
    else if (!strcmp(argv[i], "-n")) maxn = strtoull(argv[++i], 0, 0);
    else if (!strcmp(argv[i], "-calls")) maxcalls = strtoull(argv[++i], 0, 0);
    else if (!strcmp(argv[i], "-delay-ms")) delay = strtoull(argv[++i], 0, 0);
    else if (!strcmp(argv[i], "-after-ms")) after = strtoull(argv[++i], 0, 0);
    else if (!strcmp(argv[i], "-pie")) pie = 1;
    else { fprintf(stderr, "unknown option %s\n", argv[i]); return 2; }
  }
  if (!out || !lo || !size || i + 1 >= argc) { fprintf(stderr, "usage: see the header of steptrace.c\n"); return 2; }
  char **cmd = argv + i + 1;

  pid_t pid = fork();
  if (pid < 0) die("fork");
  if (pid == 0) {
    if (ptrace(PTRACE_TRACEME, 0, 0, 0)) die("TRACEME");
    execvp(cmd[0], cmd); die("execvp");
  }
  int st;
  if (waitpid(pid, &st, 0) < 0 || !WIFSTOPPED(st)) die("exec stop");
  if (ptrace(PTRACE_SETOPTIONS, pid, 0, PTRACE_O_TRACECLONE | PTRACE_O_EXITKILL)) die("SETOPTIONS");
  char mp[64]; snprintf(mp, sizeof mp, "/proc/%d/mem", pid);
  if ((memfd = open(mp, O_RDWR)) < 0) die("open mem");
  if (pie) lo += load_base(pid);
  const uint64_t hi = lo + size;

  uint8_t orig, int3 = 0xcc; mem_rw(0, lo, &orig, 1);
  int armed = 0; uint64_t t0 = now_ms();
  if (!delay) { mem_rw(1, lo, &int3, 1); armed = 1; }
  if (ptrace(PTRACE_CONT, pid, 0, 0)) die("CONT");

  // phase 1: run everything until a thread hits the entry breakpoint
  pid_t target = -1; struct user_regs_struct r; uint64_t first = 0;   // first: time of the first entry (-after-ms)
  while (target < 0) {
    pid_t tid = waitpid(-1, &st, __WALL | (armed ? 0 : WNOHANG));
    if (!armed && (first ? now_ms() - first >= after : now_ms() - t0 >= delay)) { mem_rw(1, lo, &int3, 1); armed = 1; }
    if (tid == 0) { usleep(1000); continue; }
    if (tid < 0) die("waitpid");
    if (WIFEXITED(st) || WIFSIGNALED(st)) {
      if (tid == pid) { fprintf(stderr, "program ended before entering the range\n"); return 1; }
      continue;
    }
    int sig = WSTOPSIG(st);
    if ((st >> 16) || sig == SIGSTOP) { ptrace(PTRACE_CONT, tid, 0, 0); continue; }   // clone event / new thread
    if (sig == SIGTRAP) {
      ptrace(PTRACE_GETREGS, tid, 0, &r);
      if (r.rip == lo + 1) {   // our breakpoint (armed now, or hit just before it was removed)
        if (armed) { mem_rw(1, lo, &orig, 1); armed = 0; }
        r.rip = lo; ptrace(PTRACE_SETREGS, tid, 0, &r);
        if (after && !first) {   // first entry: let it go, re-arm after `after` ms
          first = now_ms();
          fprintf(stderr, "steptrace: first entry after %lu ms; tracing the next entry %lu ms later\n",
                  (unsigned long)(first - t0), (unsigned long)after);
          ptrace(PTRACE_CONT, tid, 0, 0); continue;
        }
        if (first && now_ms() - first < after) { ptrace(PTRACE_CONT, tid, 0, 0); continue; }   // stale hit
        target = tid; break;
      }
      ptrace(PTRACE_CONT, tid, 0, 0); continue;
    }
    ptrace(PTRACE_CONT, tid, 0, sig);
  }
  fprintf(stderr, "steptrace: thread %d entered 0x%lx after %lu ms\n", target, (unsigned long)lo,
          (unsigned long)(now_ms() - t0));

  // phase 2: single-step the target; other threads keep running
  FILE *f = fopen(out, "wb"); if (!f) die(out);
  char magic[8] = {'S', 'T', 'E', 'P', 'T', 'R', 'C', '1'}; uint64_t hdr[3] = {lo, size, 0};
  fwrite(magic, 1, 8, f); fwrite(hdr, 8, 3, f);
  uint64_t n = 0, calls = 0, steps = 0, rec[17]; int prev_in = 1, pend = 0;
  for (;;) {
    if (ptrace(PTRACE_GETREGS, target, 0, &r)) die("GETREGS");
    int in = r.rip >= lo && r.rip < hi;
    if (prev_in && !in) {
      calls++;
      if (maxcalls && calls >= maxcalls) break;
    }
    prev_in = in;
    if (in) {
      put_regs(rec, &r); fwrite(rec, 8, 17, f);
      if (++n >= maxn) break;
    }
    if (ptrace(PTRACE_SINGLESTEP, target, 0, pend)) die("SINGLESTEP");
    pend = 0;
    if (waitpid(target, &st, __WALL) < 0) die("waitpid target");
    if (WIFEXITED(st) || WIFSIGNALED(st)) { fprintf(stderr, "target thread ended\n"); break; }
    int sig = WSTOPSIG(st);
    if (sig != SIGTRAP && sig != SIGSTOP && !(st >> 16)) pend = sig;   // deliver real signals on the next step
    if (++steps % 4096 == 0) {   // let other threads that stopped (late breakpoint hits, new threads) go on
      pid_t tid;
      while ((tid = waitpid(-1, &st, __WALL | WNOHANG)) > 0) {
        if (!WIFSTOPPED(st)) continue;
        int s = WSTOPSIG(st);
        if (s == SIGTRAP) {
          struct user_regs_struct q; ptrace(PTRACE_GETREGS, tid, 0, &q);
          if (q.rip == lo + 1) { q.rip = lo; ptrace(PTRACE_SETREGS, tid, 0, &q); }
          s = 0;
        }
        if (s == SIGSTOP || (st >> 16)) s = 0;
        ptrace(PTRACE_CONT, tid, 0, s);
      }
    }
  }
  hdr[2] = n;
  fseek(f, 8, SEEK_SET); fwrite(hdr, 8, 3, f); fclose(f);
  fprintf(stderr, "steptrace: %lu instructions recorded, %lu steps, %lu returns, %lu ms\n", (unsigned long)n,
          (unsigned long)steps, (unsigned long)calls, (unsigned long)(now_ms() - t0));
  kill(pid, SIGKILL);
  return 0;
}
