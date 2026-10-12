"""Bytecode instruction set.

Code is a flat list of ints: an opcode followed by its operands.  The
number and meaning of operands per opcode is listed in OPERANDS, which the
assembler, the peephole optimizer and the disassembler all share.

Operand kinds:
  const  - index into the function's constant pool
  name   - index into the constant pool of a string (variable/attr name)
  slot   - local variable slot (relative to frame base)
  upval  - index into the closure's upvalue list
  jump   - absolute code offset
  count  - an integer count (arguments, items, ...)
  binop  - an opcode number of a binary operator (used by fused ops)
"""

OPERANDS = {}
NAMES = {}


def _def(name, *operands):
    code = len(NAMES)
    NAMES[code] = name
    OPERANDS[code] = operands
    globals()[name] = code
    return code


# --- stack / constants ---
_def("CONST", "const")
_def("NULL")
_def("TRUE")
_def("FALSE")
_def("POP")
_def("DUP")
_def("DUP2")
# --- variables ---
_def("GET_LOCAL", "slot")
_def("SET_LOCAL", "slot")          # store top into slot, leave it on stack
_def("GET_UPVALUE", "upval")
_def("SET_UPVALUE", "upval")
_def("CLOSE_UPVALUE")              # close upvalue for stack top, then pop
_def("GET_GLOBAL", "name")
_def("SET_GLOBAL", "name")
_def("DEFINE_GLOBAL", "name")
# --- attributes / indexing ---
_def("GET_ATTR", "name")
_def("SET_ATTR", "name")
_def("GET_INDEX")
_def("SET_INDEX")
_def("GET_SUPER", "name")
# --- arithmetic / logic ---
_def("ADD")
_def("SUB")
_def("MUL")
_def("DIV")
_def("FLOORDIV")
_def("MOD")
_def("POW")
_def("NEG")
_def("NOT")
_def("EQ")
_def("NE")
_def("LT")
_def("LE")
_def("GT")
_def("GE")
_def("IN")
# --- control flow ---
_def("JUMP", "jump")
_def("JUMP_IF_FALSE", "jump")       # pops condition
_def("JUMP_IF_TRUE", "jump")        # pops condition
_def("JUMP_IF_FALSE_OR_POP", "jump")
_def("JUMP_IF_TRUE_OR_POP", "jump")
_def("GET_ITER")
_def("FOR_ITER", "jump")            # push next item, or jump when exhausted
# --- functions / calls ---
_def("CALL", "count")
_def("INVOKE", "name", "count")     # obj.name(args) without a bound method
_def("SUPER_INVOKE", "name", "count")
_def("CLOSURE", "const")
_def("RETURN")
# --- classes ---
_def("CLASS", "name")
_def("INHERIT")
_def("METHOD", "name")
# --- collections ---
_def("BUILD_LIST", "count")
_def("BUILD_DICT", "count")
# --- exceptions / modules ---
_def("SETUP_TRY", "jump")
_def("POP_TRY")
_def("THROW")
_def("IMPORT", "const")
# --- superinstructions produced by the peephole optimizer ---
_def("GET_LOCAL2", "slot", "slot")
_def("STORE_LOCAL", "slot")         # SET_LOCAL + POP
_def("STORE_GLOBAL", "name")        # SET_GLOBAL + POP
_def("ADD_CONST", "const")          # CONST k + ADD
_def("SUB_CONST", "const")          # CONST k + SUB
_def("CMP_JUMP_IF_FALSE", "binop", "jump")  # LT/LE/GT/GE/EQ/NE + JUMP_IF_FALSE
_def("RETURN_LOCAL", "slot")        # GET_LOCAL + RETURN
_def("LOCAL_ADD_CONST", "slot", "const")   # GET_LOCAL s + ADD_CONST k
_def("LOCAL_SUB_CONST", "slot", "const")   # GET_LOCAL s + SUB_CONST k
_def("INCR_LOCAL", "slot", "const")        # LOCAL_ADD_CONST s k + STORE_LOCAL s
_def("LOCAL_CONST_CMP_JUMP", "slot", "const", "binop", "jump")  # GET_LOCAL, CONST, CMP_JUMP_IF_FALSE
_def("LOCAL2_CMP_JUMP", "slot", "slot", "binop", "jump")         # GET_LOCAL2 + CMP_JUMP_IF_FALSE

JUMP_OPS = {code for code, ops in OPERANDS.items() if "jump" in ops}
BINARY_OPS = {ADD: "+", SUB: "-", MUL: "*", DIV: "/", FLOORDIV: "//", MOD: "%",
              POW: "**", EQ: "==", NE: "!=", LT: "<", LE: "<=", GT: ">", GE: ">=",
              IN: "in"}
COMPARE_OPS = {EQ, NE, LT, LE, GT, GE}
TERMINATORS = {JUMP, RETURN, THROW, RETURN_LOCAL}
