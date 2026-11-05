# data-dependent branch: accumulate only the odd elements of an array
#   rdi = array of qwords, rcx = i, rsi = n; r8 = sum of odd elements, r9 = their count
    mov rax, [rdi + rcx*8]
    test al, 1
    jz 2f
    add r8, rax
    inc r9
2:
    inc rcx
    cmp rcx, rsi
    jne 1b
