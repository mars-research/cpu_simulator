# variable-length inner loop (bucket probing): element i gives a length L = a[i] & 3; the inner loop loads
# tab[d * 64] for d = L .. 1. rdi = a, rbx = tab, rcx = i, rsi = n; r8 = sum of the loaded qwords
    mov rdx, [rdi + rcx*8]
    and edx, 3
2:
    test rdx, rdx
    jz 3f
    mov rax, rdx
    shl rax, 6
    add r8, [rbx + rax]
    dec rdx
    jmp 2b
3:
    inc rcx
    cmp rcx, rsi
    jne 1b
