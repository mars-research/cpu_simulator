# gcc -O3 -march=native -DRANDOM bw_roi.c on the Xeon Max 9462: the prefetch-only t0 loops (objdump, AT&T syntax).
# Loop start offset within a 64-byte line = uiCA -alignmentOffset.

## pfo_t0 pad 0: 0x407e30..0x407e4a, start offset 48
  407e30:	mov    %r15,%rax
  407e33:	crc32  %rdx,%rax
  407e39:	and    %rsi,%rax
  407e3c:	shl    $0x6,%rax
  407e40:	inc    %rdx
  407e43:	prefetcht0 (%rdi,%rax,1)
  407e47:	cmp    %rdx,%rcx
  407e4a:	jne    407e30 <mem_worker+0x4330>

## pfo_t0 pad 8: 0x407db0..0x407deb, start offset 48
  407db0:	mov    %r15,%rdx
  407db3:	crc32  %rcx,%rdx
  407db9:	and    %rdi,%rdx
  407dbc:	shl    $0x6,%rdx
  407dc0:	prefetcht0 (%r8,%rdx,1)
  407dc5:	add    $0x1,%rax
  407dc9:	add    $0x1,%rax
  407dcd:	add    $0x1,%rax
  407dd1:	add    $0x1,%rax
  407dd5:	add    $0x1,%rax
  407dd9:	add    $0x1,%rax
  407ddd:	add    $0x1,%rax
  407de1:	add    $0x1,%rax
  407de5:	inc    %rcx
  407de8:	cmp    %rsi,%rcx
  407deb:	jne    407db0 <mem_worker+0x42b0>

## pfo_t0 pad 16: 0x407d10..0x407d6b, start offset 16
  407d10:	mov    %r15,%rdx
  407d13:	crc32  %rcx,%rdx
  407d19:	and    %rdi,%rdx
  407d1c:	shl    $0x6,%rdx
  407d20:	prefetcht0 (%r8,%rdx,1)
  407d25:	add    $0x1,%rax
  407d29:	add    $0x1,%rax
  407d2d:	add    $0x1,%rax
  407d31:	add    $0x1,%rax
  407d35:	add    $0x1,%rax
  407d39:	add    $0x1,%rax
  407d3d:	add    $0x1,%rax
  407d41:	add    $0x1,%rax
  407d45:	add    $0x1,%rax
  407d49:	add    $0x1,%rax
  407d4d:	add    $0x1,%rax
  407d51:	add    $0x1,%rax
  407d55:	add    $0x1,%rax
  407d59:	add    $0x1,%rax
  407d5d:	add    $0x1,%rax
  407d61:	add    $0x1,%rax
  407d65:	inc    %rcx
  407d68:	cmp    %rsi,%rcx
  407d6b:	jne    407d10 <mem_worker+0x4210>

## pfo_t0 pad 24: 0x407c48..0x407cc3, start offset 8
  407c48:	mov    %r15,%rdx
  407c4b:	crc32  %rcx,%rdx
  407c51:	and    %rdi,%rdx
  407c54:	shl    $0x6,%rdx
  407c58:	prefetcht0 (%r8,%rdx,1)
  407c5d:	add    $0x1,%rax
  407c61:	add    $0x1,%rax
  407c65:	add    $0x1,%rax
  407c69:	add    $0x1,%rax
  407c6d:	add    $0x1,%rax
  407c71:	add    $0x1,%rax
  407c75:	add    $0x1,%rax
  407c79:	add    $0x1,%rax
  407c7d:	add    $0x1,%rax
  407c81:	add    $0x1,%rax
  407c85:	add    $0x1,%rax
  407c89:	add    $0x1,%rax
  407c8d:	add    $0x1,%rax
  407c91:	add    $0x1,%rax
  407c95:	add    $0x1,%rax
  407c99:	add    $0x1,%rax
  407c9d:	add    $0x1,%rax
  407ca1:	add    $0x1,%rax
  407ca5:	add    $0x1,%rax
  407ca9:	add    $0x1,%rax
  407cad:	add    $0x1,%rax
  407cb1:	add    $0x1,%rax
  407cb5:	add    $0x1,%rax
  407cb9:	add    $0x1,%rax
  407cbd:	inc    %rcx
  407cc0:	cmp    %rsi,%rcx
  407cc3:	jne    407c48 <mem_worker+0x4148>

## pfo_t0 pad 32: 0x407b60..0x407bfa, start offset 32
  407b60:	mov    %r15,%rdx
  407b63:	crc32  %rcx,%rdx
  407b69:	and    %rsi,%rdx
  407b6c:	shl    $0x6,%rdx
  407b70:	prefetcht0 (%rdi,%rdx,1)
  407b74:	add    $0x1,%rax
  407b78:	add    $0x1,%rax
  407b7c:	add    $0x1,%rax
  407b80:	add    $0x1,%rax
  407b84:	add    $0x1,%rax
  407b88:	add    $0x1,%rax
  407b8c:	add    $0x1,%rax
  407b90:	add    $0x1,%rax
  407b94:	add    $0x1,%rax
  407b98:	add    $0x1,%rax
  407b9c:	add    $0x1,%rax
  407ba0:	add    $0x1,%rax
  407ba4:	add    $0x1,%rax
  407ba8:	add    $0x1,%rax
  407bac:	add    $0x1,%rax
  407bb0:	add    $0x1,%rax
  407bb4:	add    $0x1,%rax
  407bb8:	add    $0x1,%rax
  407bbc:	add    $0x1,%rax
  407bc0:	add    $0x1,%rax
  407bc4:	add    $0x1,%rax
  407bc8:	add    $0x1,%rax
  407bcc:	add    $0x1,%rax
  407bd0:	add    $0x1,%rax
  407bd4:	add    $0x1,%rax
  407bd8:	add    $0x1,%rax
  407bdc:	add    $0x1,%rax
  407be0:	add    $0x1,%rax
  407be4:	add    $0x1,%rax
  407be8:	add    $0x1,%rax
  407bec:	add    $0x1,%rax
  407bf0:	add    $0x1,%rax
  407bf4:	inc    %rcx
  407bf7:	cmp    %r8,%rcx
  407bfa:	jne    407b60 <mem_worker+0x4060>

## pfo_t0 pad 48: 0x406298..0x406372, start offset 24
  406298:	mov    %r15,%rdx
  40629b:	crc32  %rcx,%rdx
  4062a1:	and    %rdi,%rdx
  4062a4:	shl    $0x6,%rdx
  4062a8:	prefetcht0 (%rsi,%rdx,1)
  4062ac:	add    $0x1,%rax
  4062b0:	add    $0x1,%rax
  4062b4:	add    $0x1,%rax
  4062b8:	add    $0x1,%rax
  4062bc:	add    $0x1,%rax
  4062c0:	add    $0x1,%rax
  4062c4:	add    $0x1,%rax
  4062c8:	add    $0x1,%rax
  4062cc:	add    $0x1,%rax
  4062d0:	add    $0x1,%rax
  4062d4:	add    $0x1,%rax
  4062d8:	add    $0x1,%rax
  4062dc:	add    $0x1,%rax
  4062e0:	add    $0x1,%rax
  4062e4:	add    $0x1,%rax
  4062e8:	add    $0x1,%rax
  4062ec:	add    $0x1,%rax
  4062f0:	add    $0x1,%rax
  4062f4:	add    $0x1,%rax
  4062f8:	add    $0x1,%rax
  4062fc:	add    $0x1,%rax
  406300:	add    $0x1,%rax
  406304:	add    $0x1,%rax
  406308:	add    $0x1,%rax
  40630c:	add    $0x1,%rax
  406310:	add    $0x1,%rax
  406314:	add    $0x1,%rax
  406318:	add    $0x1,%rax
  40631c:	add    $0x1,%rax
  406320:	add    $0x1,%rax
  406324:	add    $0x1,%rax
  406328:	add    $0x1,%rax
  40632c:	add    $0x1,%rax
  406330:	add    $0x1,%rax
  406334:	add    $0x1,%rax
  406338:	add    $0x1,%rax
  40633c:	add    $0x1,%rax
  406340:	add    $0x1,%rax
  406344:	add    $0x1,%rax
  406348:	add    $0x1,%rax
  40634c:	add    $0x1,%rax
  406350:	add    $0x1,%rax
  406354:	add    $0x1,%rax
  406358:	add    $0x1,%rax
  40635c:	add    $0x1,%rax
  406360:	add    $0x1,%rax
  406364:	add    $0x1,%rax
  406368:	add    $0x1,%rax
  40636c:	inc    %rcx
  40636f:	cmp    %r8,%rcx
  406372:	jne    406298 <mem_worker+0x2798>
