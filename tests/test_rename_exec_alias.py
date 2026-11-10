#!/usr/bin/env python3
"""Regression: renamer KeyError on Golden Cove+ rename-time execution.

    mov rbx, rax        ; move-eliminated: RAX's physical register is shared by {RAX, RBX}
    lea r12, [rax+8]    ; 0 uops on EMR, executed at rename: R12 aliases the same register,
                        ; but is not in its alias set
    mov r12d, 1         ; overwrites R12 -> multiUseGPRDict[...].remove('R12') raised KeyError

    python3 tests/test_rename_exec_alias.py      (needs GNU as)
"""
import subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
ASM = ".intel_syntax noprefix\nmov rbx, rax\nlea r12, [rax+8]\nmov r12d, 1\n"
with tempfile.TemporaryDirectory() as d:
    s, o = Path(d) / "k.s", Path(d) / "k.o"
    s.write_text(ASM)
    subprocess.run(["as", str(s), "-o", str(o)], check=True)
    for arch in ("EMR", "SKX"):
        p = subprocess.run([sys.executable, str(ROOT / "uiCA.py"), str(o), "-arch", arch], capture_output=True, text=True)
        ok = p.returncode == 0 and "Throughput" in p.stdout
        print(f"{arch}: {'ok' if ok else 'FAIL'}")
        if not ok:
            print(p.stderr[-600:]); sys.exit(1)
