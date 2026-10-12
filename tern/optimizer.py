"""Peephole optimizer over the compiler's symbolic instruction list.

Runs before assembly, while jump targets are still `Label` objects, so it
can freely change instruction lengths.  Two adjacent instructions are only
fused when no label sits between them (i.e. nothing jumps into the middle).

Passes (repeated until nothing changes):
  * constant folding:   CONST a, CONST b, <arith>  ->  CONST (a op b)
                        CONST a, NEG               ->  CONST -a
  * superinstructions:  GET_LOCAL a, GET_LOCAL b   ->  GET_LOCAL2 a b
                        SET_LOCAL s, POP           ->  STORE_LOCAL s
                        SET_GLOBAL n, POP          ->  STORE_GLOBAL n
                        CONST k, ADD / SUB         ->  ADD_CONST k / SUB_CONST k
                        <compare>, JUMP_IF_FALSE L ->  CMP_JUMP_IF_FALSE cmp L
                        NOT, JUMP_IF_FALSE L       ->  JUMP_IF_TRUE L
                        GET_LOCAL s, RETURN        ->  RETURN_LOCAL s
  * jump threading:     JUMP L where L: JUMP M     ->  JUMP M
  * dead code removal:  instructions after JUMP/RETURN/THROW up to the next label
"""
from . import opcodes as op

FOLDABLE = {
    op.ADD: lambda a, b: a + b,
    op.SUB: lambda a, b: a - b,
    op.MUL: lambda a, b: a * b,
    op.DIV: lambda a, b: a / b,
    op.FLOORDIV: lambda a, b: a // b,
    op.MOD: lambda a, b: a % b,
}


def _is_num(v):
    return type(v) in (int, float)


def _try_fold(opcode, a, b):
    if opcode == op.ADD and type(a) is str and type(b) is str:
        if len(a) + len(b) <= 4096:
            return True, a + b
        return False, None
    if opcode not in FOLDABLE or not (_is_num(a) and _is_num(b)):
        return False, None
    if opcode in (op.DIV, op.FLOORDIV, op.MOD) and b == 0:
        return False, None              # leave it for the runtime error
    result = FOLDABLE[opcode](a, b)
    if type(result) is int and abs(result) > 2 ** 64:
        return False, None
    return True, result


def optimize(items, consts, add_const):
    from .compiler import Instr, Label

    def is_instr(x):
        return isinstance(x, Instr)

    changed = True
    while changed:
        changed = False

        # drop labels nothing jumps to, so they don't block fusion
        used = set()
        for it in items:
            if is_instr(it):
                for a in it.args:
                    if isinstance(a, Label):
                        used.add(id(a))
        items = [it for it in items if is_instr(it) or id(it) in used]

        # dead code elimination
        out = []
        dead = False
        for it in items:
            if not is_instr(it):
                dead = False
                out.append(it)
            elif not dead:
                out.append(it)
                if it.op in op.TERMINATORS:
                    dead = True
            else:
                changed = True
        items = out

        # jump threading
        target_of = {}
        for i, it in enumerate(items):
            if not is_instr(it):
                j = i + 1
                while j < len(items) and not is_instr(items[j]):
                    j += 1
                if j < len(items):
                    target_of[id(it)] = items[j]
        for it in items:
            if is_instr(it) and it.op == op.JUMP:
                seen = set()
                tgt = target_of.get(id(it.args[0]))
                while tgt is not None and tgt.op == op.JUMP and id(tgt) not in seen \
                        and tgt.args[0] is not it.args[0]:
                    seen.add(id(tgt))
                    it.args[0] = tgt.args[0]
                    changed = True
                    tgt = target_of.get(id(it.args[0]))

        # windowed rewrites
        out = []
        i = 0
        n = len(items)
        while i < n:
            a = items[i]
            b = items[i + 1] if i + 1 < n else None
            c = items[i + 2] if i + 2 < n else None
            if is_instr(a) and is_instr(b):
                # 3-instruction constant folding
                if a.op == op.CONST and b.op == op.CONST and is_instr(c) and c.op in FOLDABLE:
                    ok, value = _try_fold(c.op, consts[a.args[0]], consts[b.args[0]])
                    if ok:
                        out.append(Instr(op.CONST, [add_const(value)], a.pos))
                        i += 3
                        changed = True
                        continue
                if a.op == op.CONST and b.op == op.NEG and _is_num(consts[a.args[0]]):
                    out.append(Instr(op.CONST, [add_const(-consts[a.args[0]])], a.pos))
                    i += 2
                    changed = True
                    continue
                fused = None
                if a.op == op.GET_LOCAL and b.op == op.GET_LOCAL:
                    fused = Instr(op.GET_LOCAL2, [a.args[0], b.args[0]], b.pos)
                elif a.op == op.SET_LOCAL and b.op == op.POP:
                    fused = Instr(op.STORE_LOCAL, a.args, a.pos)
                elif a.op == op.SET_GLOBAL and b.op == op.POP:
                    fused = Instr(op.STORE_GLOBAL, a.args, a.pos)
                elif a.op == op.CONST and b.op == op.ADD and _is_num(consts[a.args[0]]):
                    fused = Instr(op.ADD_CONST, a.args, b.pos)
                elif a.op == op.CONST and b.op == op.SUB and _is_num(consts[a.args[0]]):
                    fused = Instr(op.SUB_CONST, a.args, b.pos)
                elif a.op in op.COMPARE_OPS and b.op == op.JUMP_IF_FALSE:
                    fused = Instr(op.CMP_JUMP_IF_FALSE, [a.op, b.args[0]], a.pos)
                elif a.op == op.NOT and b.op == op.JUMP_IF_FALSE:
                    fused = Instr(op.JUMP_IF_TRUE, b.args, b.pos)
                elif a.op == op.GET_LOCAL and b.op == op.RETURN:
                    fused = Instr(op.RETURN_LOCAL, a.args, b.pos)
                if fused is not None:
                    out.append(fused)
                    i += 2
                    changed = True
                    continue
            out.append(a)
            i += 1
        items = out
    return items
