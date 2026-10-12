"""Human-readable bytecode listing for a compiled function and its children."""
from . import opcodes as O
from .objects import FunctionProto, to_str


def _const_repr(v):
    if isinstance(v, FunctionProto):
        return "<fn %s>" % v.name
    return to_str(v, quote=True)


def disassemble_proto(proto, out_lines):
    kind = proto.kind
    out_lines.append("== %s %s (arity %d, %d upvalue%s) [%s:%d] ==" % (
        kind, proto.name, proto.arity, len(proto.upvalue_specs),
        "" if len(proto.upvalue_specs) == 1 else "s", proto.filename, proto.line))
    if proto.upvalue_specs:
        specs = ", ".join("%s%d" % ("local " if is_local else "upval ", idx)
                          for is_local, idx in proto.upvalue_specs)
        out_lines.append("   captures: " + specs)
    code = proto.code
    ip = 0
    last_line = None
    while ip < len(code):
        opcode = code[ip]
        name = O.NAMES.get(opcode, "??? %r" % opcode)
        kinds = O.OPERANDS.get(opcode, ())
        args = code[ip + 1: ip + 1 + len(kinds)]
        line = proto.positions[ip][0] if ip < len(proto.positions) else 0
        line_col = "%4d" % line if line != last_line else "   |"
        last_line = line
        parts = []
        for kind, a in zip(kinds, args):
            if kind == "const":
                parts.append("%d (%s)" % (a, _const_repr(proto.consts[a])))
            elif kind == "name":
                parts.append("%d '%s'" % (a, proto.consts[a]))
            elif kind == "jump":
                parts.append("-> %d" % a)
            elif kind == "slot":
                parts.append("slot %d" % a)
            elif kind == "upval":
                parts.append("upval %d" % a)
            elif kind == "binop":
                parts.append(O.BINARY_OPS.get(a, "?"))
            else:
                parts.append(str(a))
        out_lines.append(("%05d %s  %-20s %s" % (ip, line_col, name, ", ".join(parts))).rstrip())
        ip += 1 + len(kinds)
    for c in proto.consts:
        if isinstance(c, FunctionProto):
            out_lines.append("")
            disassemble_proto(c, out_lines)


def disassemble(proto):
    lines = []
    disassemble_proto(proto, lines)
    return "\n".join(lines)
