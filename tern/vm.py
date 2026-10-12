"""Stack-based virtual machine.

Design notes
------------
* One value stack shared by all frames.  A frame's locals are the stack
  slots starting at `frame.base` (slot 0 = callee / self).
* Tern calls never recurse in Python: CALL pushes a Frame on `self.frames`
  and the dispatch loop just switches to it.  Deep Tern recursion is bounded
  only by MAX_FRAMES, not Python's recursion limit.
* Natives that call back into Tern (map, filter, sort with a key, ...) use
  `call_value`, which runs a nested dispatch loop until that one call
  returns (`stop_depth`).  Exceptions crossing that boundary travel as the
  Python exception TernThrow and resume unwinding in the outer loop.
* Exceptions: SETUP_TRY pushes (handler_ip, stack_height) on the current
  frame's handler list.  Throwing unwinds frames until one has a handler,
  closes upvalues above the saved height, truncates the stack, pushes the
  exception value and jumps to the handler.
* Closures capture variables through Upvalue objects (see objects.py).
  Open upvalues are indexed by absolute stack slot in `open_upvalues`; they
  are closed whenever the slots they point at are discarded (CLOSE_UPVALUE,
  RETURN, exception unwinding), so no closure ever refers to a dead slot.
"""
import os
import sys

from . import opcodes as O
from .errors import RuntimeFault, TernSyntaxError, TernThrow, TernUncaught, caret_snippet
from .objects import (BoundMethod, Closure, Instance, Module, NativeFunction, NativeMethod,
                      TernClass, Upvalue, check_hashable, to_str, type_name, values_equal)

MAX_FRAMES = 200_000

# Opcode numbers bound as module globals for the dispatch loop.
CONST = O.CONST; NULL = O.NULL; TRUE = O.TRUE; FALSE = O.FALSE; POP = O.POP
DUP = O.DUP; DUP2 = O.DUP2; GET_LOCAL = O.GET_LOCAL; SET_LOCAL = O.SET_LOCAL
GET_UPVALUE = O.GET_UPVALUE; SET_UPVALUE = O.SET_UPVALUE; CLOSE_UPVALUE = O.CLOSE_UPVALUE
GET_GLOBAL = O.GET_GLOBAL; SET_GLOBAL = O.SET_GLOBAL; DEFINE_GLOBAL = O.DEFINE_GLOBAL
GET_ATTR = O.GET_ATTR; SET_ATTR = O.SET_ATTR; GET_INDEX = O.GET_INDEX; SET_INDEX = O.SET_INDEX
GET_SUPER = O.GET_SUPER; ADD = O.ADD; SUB = O.SUB; MUL = O.MUL; DIV = O.DIV
FLOORDIV = O.FLOORDIV; MOD = O.MOD; POW = O.POW; NEG = O.NEG; NOT = O.NOT; EQ = O.EQ
NE = O.NE; LT = O.LT; LE = O.LE; GT = O.GT; GE = O.GE; IN = O.IN; JUMP = O.JUMP
JUMP_IF_FALSE = O.JUMP_IF_FALSE; JUMP_IF_TRUE = O.JUMP_IF_TRUE
JUMP_IF_FALSE_OR_POP = O.JUMP_IF_FALSE_OR_POP; JUMP_IF_TRUE_OR_POP = O.JUMP_IF_TRUE_OR_POP
GET_ITER = O.GET_ITER; FOR_ITER = O.FOR_ITER; CALL = O.CALL; INVOKE = O.INVOKE
SUPER_INVOKE = O.SUPER_INVOKE; CLOSURE = O.CLOSURE; RETURN = O.RETURN; CLASS = O.CLASS
INHERIT = O.INHERIT; METHOD = O.METHOD; BUILD_LIST = O.BUILD_LIST; BUILD_DICT = O.BUILD_DICT
SETUP_TRY = O.SETUP_TRY; POP_TRY = O.POP_TRY; THROW = O.THROW; IMPORT = O.IMPORT
GET_LOCAL2 = O.GET_LOCAL2; STORE_LOCAL = O.STORE_LOCAL; STORE_GLOBAL = O.STORE_GLOBAL
ADD_CONST = O.ADD_CONST; SUB_CONST = O.SUB_CONST; CMP_JUMP_IF_FALSE = O.CMP_JUMP_IF_FALSE
RETURN_LOCAL = O.RETURN_LOCAL; LOCAL_ADD_CONST = O.LOCAL_ADD_CONST
LOCAL_SUB_CONST = O.LOCAL_SUB_CONST; INCR_LOCAL = O.INCR_LOCAL
LOCAL_CONST_CMP_JUMP = O.LOCAL_CONST_CMP_JUMP; LOCAL2_CMP_JUMP = O.LOCAL2_CMP_JUMP

_MISSING = object()
_DONE = object()


class Frame:
    __slots__ = ("closure", "code", "consts", "ip", "base", "handlers", "globals", "upvalues")

    def __init__(self, closure, base):
        self.closure = closure
        proto = closure.proto
        self.code = proto.code
        self.consts = proto.consts
        self.ip = 0
        self.base = base
        self.handlers = None
        self.globals = closure.globals
        self.upvalues = closure.upvalues


# ---------------------------------------------------------------------------
# slow paths for operators (fast int paths are inlined in the loop)
# ---------------------------------------------------------------------------

def _num(v):
    t = type(v)
    return t is int or t is float


def binary_op(o, a, b):
    if _num(a) and _num(b):
        if o == ADD:
            return a + b
        if o == SUB:
            return a - b
        if o == MUL:
            return a * b
        if o == DIV:
            if b == 0:
                raise RuntimeFault("ZeroDivisionError", "division by zero")
            return a / b
        if o == FLOORDIV:
            if b == 0:
                raise RuntimeFault("ZeroDivisionError", "integer division by zero")
            return a // b
        if o == MOD:
            if b == 0:
                raise RuntimeFault("ZeroDivisionError", "modulo by zero")
            return a % b
        if o == POW:
            try:
                r = a ** b
            except OverflowError:
                raise RuntimeFault("ValueError", "numeric overflow in '**'")
            if type(r) is complex:
                raise RuntimeFault("ValueError", "negative number raised to a fractional power")
            return r
    ta, tb = type(a), type(b)
    if o == ADD:
        if ta is str and tb is str:
            return a + b
        if ta is list and tb is list:
            return a + b
        if ta is str or tb is str:
            raise RuntimeFault("TypeError", "cannot add %s and %s (use str() to convert)"
                               % (type_name(a), type_name(b)))
    elif o == MUL:
        if (ta is str or ta is list) and tb is int:
            return a * b
        if ta is int and (tb is str or tb is list):
            return a * b
    elif o == MOD and ta is str:
        raise RuntimeFault("TypeError", "string formatting with '%' is not supported")
    raise RuntimeFault("TypeError", "unsupported operand types for %s: %s and %s"
                       % (O.BINARY_OPS[o], type_name(a), type_name(b)))


def compare_op(o, a, b):
    if o == EQ:
        return values_equal(a, b)
    if o == NE:
        return not values_equal(a, b)
    if (_num(a) and _num(b)) or (type(a) is str and type(b) is str):
        if o == LT:
            return a < b
        if o == LE:
            return a <= b
        if o == GT:
            return a > b
        return a >= b
    if type(a) is list and type(b) is list:
        # lexicographic comparison, element-wise with Tern rules
        for x, y in zip(a, b):
            if not values_equal(x, y):
                return compare_op(o, x, y)
        return compare_op(o, len(a), len(b))
    raise RuntimeFault("TypeError", "'%s' not supported between %s and %s"
                       % (O.BINARY_OPS[o], type_name(a), type_name(b)))


def contains(a, b):
    tb = type(b)
    if tb is list:
        for x in b:
            if values_equal(a, x):
                return True
        return False
    if tb is str:
        if type(a) is not str:
            raise RuntimeFault("TypeError", "'in <string>' requires a string, got %s" % type_name(a))
        return a in b
    if tb is dict:
        check_hashable(a)
        return a in b
    raise RuntimeFault("TypeError", "'in' not supported for %s" % type_name(b))


def check_index(obj, idx):
    if type(idx) is not int:
        raise RuntimeFault("TypeError", "%s index must be an int, got %s"
                           % (type_name(obj), type_name(idx)))
    n = len(obj)
    if idx < -n or idx >= n:
        raise RuntimeFault("IndexError", "%s index %d out of range (length %d)"
                           % (type_name(obj), idx, n))
    return idx


def get_index(obj, idx):
    t = type(obj)
    if t is list or t is str:
        return obj[check_index(obj, idx)]
    if t is dict:
        check_hashable(idx)
        try:
            return obj[idx]
        except KeyError:
            raise RuntimeFault("KeyError", "key %s not found" % to_str(idx, quote=True))
    raise RuntimeFault("TypeError", "%s is not indexable" % type_name(obj))


def set_index(obj, idx, value):
    t = type(obj)
    if t is list:
        obj[check_index(obj, idx)] = value
    elif t is dict:
        obj[check_hashable(idx)] = value
    elif t is str:
        raise RuntimeFault("TypeError", "strings are immutable")
    else:
        raise RuntimeFault("TypeError", "%s does not support item assignment" % type_name(obj))


class VM:
    def __init__(self, out=None, optimize=True, max_frames=MAX_FRAMES):
        self.stack = []
        self.frames = []
        self.open_upvalues = {}
        self.builtins = {}
        self.modules = {}
        self.out = out if out is not None else sys.stdout
        self.optimize = optimize
        self.max_frames = max_frames
        self.loading = set()
        from . import stdlib
        self.type_methods = stdlib.TYPE_METHODS
        stdlib.install(self)
        self.run_source(stdlib.PRELUDE, "<prelude>", self.builtins)

    # ------------------------------------------------------------------ API
    def compile(self, source, filename, repl=False):
        from .compiler import compile_source
        return compile_source(source, filename, optimize=self.optimize, repl=repl)

    def run_source(self, source, filename="<input>", globals_=None, repl=False):
        """Compile and run source; raises TernSyntaxError or TernUncaught."""
        proto = self.compile(source, filename, repl=repl)
        if globals_ is None:
            globals_ = {}
        return self.run_proto(proto, globals_)

    def run_proto(self, proto, globals_):
        closure = Closure(proto, [], globals_)
        try:
            return self.call_value(closure, [])
        except TernThrow as t:
            raise TernUncaught(t.value, t.trace, self.format_uncaught(t.value, t.trace))

    def run_file(self, path):
        path = os.path.abspath(path)
        with open(path, encoding="utf-8") as f:
            source = f.read()
        module = Module(os.path.splitext(os.path.basename(path))[0], path, {})
        self.modules[path] = module
        return self.run_source(source, path, module.globals)

    def call_value(self, callee, args):
        """Call any Tern callable from Python and return its result."""
        stack = self.stack
        stop_depth = len(self.frames)
        stack.append(callee)
        stack.extend(args)
        if self.call_other(callee, len(args)):
            return self.run(stop_depth)
        return stack.pop()

    # ------------------------------------------------------------ helpers
    def make_error(self, kind, message):
        cls = self.builtins.get(kind)
        if type(cls) is not TernClass:
            cls = self.builtins["Error"]
        inst = Instance(cls)
        inst.fields["message"] = message
        return inst

    def capture(self, index):
        uv = self.open_upvalues.get(index)
        if uv is None:
            uv = Upvalue(index)
            self.open_upvalues[index] = uv
        return uv

    def close_upvalues(self, from_index):
        ou = self.open_upvalues
        if not ou:
            return
        stack = self.stack
        for idx in [i for i in ou if i >= from_index]:
            uv = ou.pop(idx)
            uv.value = stack[idx]
            uv.index = -1

    def push_frame(self, closure, n):
        proto = closure.proto
        if proto.arity != n:
            raise RuntimeFault("TypeError", "%s() takes %d argument%s but %d %s given"
                               % (proto.name, proto.arity, "" if proto.arity == 1 else "s",
                                  n, "was" if n == 1 else "were"))
        if len(self.frames) >= self.max_frames:
            raise RuntimeFault("StackOverflowError", "maximum recursion depth exceeded (%d frames)"
                               % self.max_frames)
        self.frames.append(Frame(closure, len(self.stack) - n - 1))

    def check_native_arity(self, name, n, lo, hi):
        if n < lo or (hi is not None and n > hi):
            if hi is None:
                expect = "at least %d" % lo
            elif lo == hi:
                expect = "exactly %d" % lo
            else:
                expect = "%d to %d" % (lo, hi)
            raise RuntimeFault("TypeError", "%s() takes %s argument%s but %d %s given"
                               % (name, expect, "" if (hi or lo) == 1 else "s", n,
                                  "was" if n == 1 else "were"))

    def call_other(self, callee, n):
        """Generic call. Returns True if a new Tern frame was pushed,
        False if the result has already replaced callee+args on the stack."""
        stack = self.stack
        t = type(callee)
        if t is Closure:
            self.push_frame(callee, n)
            return True
        if t is NativeFunction:
            self.check_native_arity(callee.name, n, callee.min_args, callee.max_args)
            args = stack[len(stack) - n:]
            result = callee.fn(self, args)
            del stack[len(stack) - n - 1:]
            stack.append(result)
            return False
        if t is BoundMethod:
            stack[-n - 1] = callee.receiver
            self.push_frame(callee.method, n)
            return True
        if t is NativeMethod:
            self.check_native_arity(callee.name, n, callee.min_args, callee.max_args)
            args = stack[len(stack) - n:]
            result = callee.fn(self, callee.receiver, args)
            del stack[len(stack) - n - 1:]
            stack.append(result)
            return False
        if t is TernClass:
            inst = Instance(callee)
            stack[-n - 1] = inst
            init = callee.methods.get("init")
            if init is not None:
                self.push_frame(init, n)
                return True
            if n != 0:
                raise RuntimeFault("TypeError", "%s() takes 0 arguments but %d %s given"
                                   % (callee.name, n, "was" if n == 1 else "were"))
            del stack[len(stack) - n:]
            return False
        raise RuntimeFault("TypeError", "%s is not callable" % type_name(callee))

    def get_attr(self, obj, name):
        t = type(obj)
        if t is Instance:
            v = obj.fields.get(name, _MISSING)
            if v is not _MISSING:
                return v
            m = obj.cls.methods.get(name)
            if m is not None:
                return BoundMethod(obj, m)
            raise RuntimeFault("AttributeError", "%s instance has no attribute '%s'"
                               % (obj.cls.name, name))
        if t is Module:
            v = obj.globals.get(name, _MISSING)
            if v is _MISSING:
                raise RuntimeFault("AttributeError", "module '%s' has no attribute '%s'"
                                   % (obj.name, name))
            return v
        if t is TernClass:
            if name == "name":
                return obj.name
            if name == "superclass":
                return obj.superclass
            m = obj.methods.get(name)
            if m is not None:
                return m
            raise RuntimeFault("AttributeError", "class %s has no method '%s'" % (obj.name, name))
        table = self.type_methods.get(t)
        if table is not None:
            entry = table.get(name)
            if entry is not None:
                return NativeMethod(name, entry[0], obj, entry[1], entry[2])
        raise RuntimeFault("AttributeError", "%s has no attribute '%s'" % (type_name(obj), name))

    def import_module(self, path, importer):
        if not path.endswith(".tern"):
            path += ".tern"
        candidates = []
        if importer and os.path.isfile(importer):
            candidates.append(os.path.join(os.path.dirname(importer), path))
        candidates.append(os.path.join(os.getcwd(), path))
        for extra in os.environ.get("TERN_PATH", "").split(os.pathsep):
            if extra:
                candidates.append(os.path.join(extra, path))
        for cand in candidates:
            full = os.path.abspath(cand)
            if os.path.isfile(full):
                break
        else:
            raise RuntimeFault("ImportError", "cannot find module '%s'" % path)
        module = self.modules.get(full)
        if module is not None:
            return module                      # cached (or partially loaded: cycle)
        with open(full, encoding="utf-8") as f:
            source = f.read()
        proto = self.compile(source, full)
        module = Module(os.path.splitext(os.path.basename(full))[0], full, {})
        self.modules[full] = module
        try:
            self.call_value(Closure(proto, [], module.globals), [])
        except BaseException:
            del self.modules[full]
            raise
        return module

    # ---------------------------------------------------------- tracebacks
    def capture_trace(self):
        trace = []
        for f in self.frames:
            proto = f.closure.proto
            i = max(0, min(f.ip - 1, len(proto.positions) - 1))
            line, col = proto.positions[i] if proto.positions else (0, 0)
            trace.append((proto.name, proto.filename, line, col, proto.source))
        return trace

    def describe_exception(self, value):
        if type(value) is Instance and value.cls.is_subclass_of(self.builtins.get("Error")):
            msg = value.fields.get("message")
            return "%s: %s" % (value.cls.name, to_str(msg))
        try:
            return "Uncaught exception: %s" % to_str(value, self, quote=True)
        except Exception:
            return "Uncaught exception: <%s>" % type_name(value)

    def format_uncaught(self, value, trace):
        lines = ["Traceback (most recent call last):"]
        trace = trace or []
        shown = trace
        if len(trace) > 20:
            shown = trace[:10] + [None] + trace[-10:]
        for entry in shown:
            if entry is None:
                lines.append("  ... %d more frames ..." % (len(trace) - 20))
                continue
            name, filename, line, col, _ = entry
            lines.append("  at %s (%s:%d:%d)" % (name, filename, line, col))
        lines.append(self.describe_exception(value))
        if trace:
            name, filename, line, col, source = trace[-1]
            lines.append("  --> %s:%d:%d" % (filename, line, col))
            snippet = caret_snippet(source, line, col)
            if snippet:
                lines.append(snippet)
        return "\n".join(lines)

    # ------------------------------------------------------------ unwinding
    def unwind(self, value, trace, stop_depth):
        frames = self.frames
        stack = self.stack
        while len(frames) > stop_depth:
            f = frames[-1]
            if f.handlers:
                handler_ip, height = f.handlers.pop()
                self.close_upvalues(height)
                del stack[height:]
                stack.append(value)
                f.ip = handler_ip
                return f
            self.close_upvalues(f.base)
            del stack[f.base:]
            frames.pop()
        raise TernThrow(value, trace)

    # ------------------------------------------------------------ dispatch
    def run(self, stop_depth):
        stack = self.stack
        frames = self.frames
        push = stack.append
        pop = stack.pop
        frame = frames[-1]
        code = frame.code
        consts = frame.consts
        ip = frame.ip
        base = frame.base
        upvals = frame.upvalues
        globals_ = frame.globals
        builtins = self.builtins
        max_frames = self.max_frames

        while True:
            try:
                while True:
                    o = code[ip]
                    ip += 1
                    # Dispatch order matters: each failed `elif` costs ~15ns, so
                    # the hottest instructions (incl. superinstructions) go first.
                    if o == GET_LOCAL:
                        push(stack[base + code[ip]])
                        ip += 1
                    elif o == LOCAL_CONST_CMP_JUMP:
                        a = stack[base + code[ip]]
                        b = consts[code[ip + 1]]
                        cmp = code[ip + 2]
                        if type(a) is int and type(b) is int:
                            if cmp == LT:
                                r = a < b
                            elif cmp == GT:
                                r = a > b
                            elif cmp == LE:
                                r = a <= b
                            elif cmp == GE:
                                r = a >= b
                            elif cmp == EQ:
                                r = a == b
                            else:
                                r = a != b
                        else:
                            r = compare_op(cmp, a, b)
                        if r:
                            ip += 4
                        else:
                            ip = code[ip + 3]
                    elif o == GET_GLOBAL:
                        name = consts[code[ip]]
                        ip += 1
                        v = globals_.get(name, _MISSING)
                        if v is _MISSING:
                            v = builtins.get(name, _MISSING)
                            if v is _MISSING:
                                raise RuntimeFault("NameError", "undefined variable '%s'" % name)
                        push(v)
                    elif o == CALL:
                        n = code[ip]
                        ip += 1
                        callee = stack[-1 - n]
                        if type(callee) is Closure:
                            proto = callee.proto
                            if proto.arity != n or len(frames) >= max_frames:
                                frame.ip = ip
                                self.push_frame(callee, n)   # raises the right error
                            frame.ip = ip
                            frame = Frame(callee, len(stack) - n - 1)
                            frames.append(frame)
                            code = proto.code
                            consts = proto.consts
                            ip = 0
                            base = frame.base
                            upvals = callee.upvalues
                            globals_ = callee.globals
                        else:
                            frame.ip = ip
                            if self.call_other(callee, n):
                                frame = frames[-1]
                                code = frame.code
                                consts = frame.consts
                                ip = 0
                                base = frame.base
                                upvals = frame.upvalues
                                globals_ = frame.globals
                    elif o == LOCAL_SUB_CONST:
                        a = stack[base + code[ip]]
                        b = consts[code[ip + 1]]
                        ip += 2
                        if type(a) is int and type(b) is int:
                            push(a - b)
                        else:
                            push(binary_op(SUB, a, b))
                    elif o == RETURN_LOCAL:
                        result = stack[base + code[ip]]
                        if self.open_upvalues:
                            self.close_upvalues(base)
                        del stack[base:]
                        frames.pop()
                        if len(frames) == stop_depth:
                            return result
                        push(result)
                        frame = frames[-1]
                        code = frame.code
                        consts = frame.consts
                        ip = frame.ip
                        base = frame.base
                        upvals = frame.upvalues
                        globals_ = frame.globals
                    elif o == RETURN:
                        result = pop()
                        if self.open_upvalues:
                            self.close_upvalues(base)
                        del stack[base:]
                        frames.pop()
                        if len(frames) == stop_depth:
                            return result
                        push(result)
                        frame = frames[-1]
                        code = frame.code
                        consts = frame.consts
                        ip = frame.ip
                        base = frame.base
                        upvals = frame.upvalues
                        globals_ = frame.globals
                    elif o == CONST:
                        push(consts[code[ip]])
                        ip += 1
                    elif o == INCR_LOCAL:
                        i = base + code[ip]
                        a = stack[i]
                        b = consts[code[ip + 1]]
                        ip += 2
                        if type(a) is int and type(b) is int:
                            stack[i] = a + b
                        else:
                            stack[i] = binary_op(ADD, a, b)
                    elif o == LOCAL2_CMP_JUMP:
                        a = stack[base + code[ip]]
                        b = stack[base + code[ip + 1]]
                        cmp = code[ip + 2]
                        if type(a) is int and type(b) is int:
                            if cmp == LT:
                                r = a < b
                            elif cmp == GT:
                                r = a > b
                            elif cmp == LE:
                                r = a <= b
                            elif cmp == GE:
                                r = a >= b
                            elif cmp == EQ:
                                r = a == b
                            else:
                                r = a != b
                        else:
                            r = compare_op(cmp, a, b)
                        if r:
                            ip += 4
                        else:
                            ip = code[ip + 3]
                    elif o == LOCAL_ADD_CONST:
                        a = stack[base + code[ip]]
                        b = consts[code[ip + 1]]
                        ip += 2
                        if type(a) is int and type(b) is int:
                            push(a + b)
                        else:
                            push(binary_op(ADD, a, b))
                    elif o == GET_LOCAL2:
                        push(stack[base + code[ip]])
                        push(stack[base + code[ip + 1]])
                        ip += 2
                    elif o == ADD:
                        b = pop()
                        a = stack[-1]
                        if type(a) is int and type(b) is int:
                            stack[-1] = a + b
                        else:
                            stack[-1] = binary_op(ADD, a, b)
                    elif o == SUB:
                        b = pop()
                        a = stack[-1]
                        if type(a) is int and type(b) is int:
                            stack[-1] = a - b
                        else:
                            stack[-1] = binary_op(SUB, a, b)
                    elif o == ADD_CONST:
                        b = consts[code[ip]]
                        ip += 1
                        a = stack[-1]
                        if type(a) is int and type(b) is int:
                            stack[-1] = a + b
                        else:
                            stack[-1] = binary_op(ADD, a, b)
                    elif o == SUB_CONST:
                        b = consts[code[ip]]
                        ip += 1
                        a = stack[-1]
                        if type(a) is int and type(b) is int:
                            stack[-1] = a - b
                        else:
                            stack[-1] = binary_op(SUB, a, b)
                    elif o == CMP_JUMP_IF_FALSE:
                        cmp = code[ip]
                        b = pop()
                        a = pop()
                        if type(a) is int and type(b) is int:
                            if cmp == LT:
                                r = a < b
                            elif cmp == GT:
                                r = a > b
                            elif cmp == LE:
                                r = a <= b
                            elif cmp == GE:
                                r = a >= b
                            elif cmp == EQ:
                                r = a == b
                            else:
                                r = a != b
                        else:
                            r = compare_op(cmp, a, b)
                        if r:
                            ip += 2
                        else:
                            ip = code[ip + 1]
                    elif o == LT:
                        b = pop()
                        a = stack[-1]
                        if type(a) is int and type(b) is int:
                            stack[-1] = a < b
                        else:
                            stack[-1] = compare_op(LT, a, b)
                    elif o == JUMP_IF_FALSE:
                        if pop():
                            ip += 1
                        else:
                            ip = code[ip]
                    elif o == STORE_LOCAL:
                        stack[base + code[ip]] = pop()
                        ip += 1
                    elif o == JUMP:
                        ip = code[ip]
                    elif o == POP:
                        pop()
                    elif o == SET_LOCAL:
                        stack[base + code[ip]] = stack[-1]
                        ip += 1
                    elif o == INVOKE:
                        name = consts[code[ip]]
                        n = code[ip + 1]
                        ip += 2
                        recv = stack[-1 - n]
                        frame.ip = ip
                        if type(recv) is Instance:
                            v = recv.fields.get(name, _MISSING)
                            if v is not _MISSING:
                                stack[-1 - n] = v
                                pushed = self.call_other(v, n)
                            else:
                                method = recv.cls.methods.get(name)
                                if method is None:
                                    raise RuntimeFault("AttributeError",
                                                       "%s instance has no method '%s'"
                                                       % (recv.cls.name, name))
                                self.push_frame(method, n)
                                pushed = True
                        else:
                            table = self.type_methods.get(type(recv))
                            entry = table.get(name) if table is not None else None
                            if entry is not None:
                                self.check_native_arity(name, n, entry[1], entry[2])
                                args = stack[len(stack) - n:]
                                result = entry[0](self, recv, args)
                                del stack[len(stack) - n - 1:]
                                push(result)
                                pushed = False
                            else:
                                v = self.get_attr(recv, name)
                                stack[-1 - n] = v
                                pushed = self.call_other(v, n)
                        if pushed:
                            frame = frames[-1]
                            code = frame.code
                            consts = frame.consts
                            ip = 0
                            base = frame.base
                            upvals = frame.upvalues
                            globals_ = frame.globals
                    elif o == GET_ATTR:
                        name = consts[code[ip]]
                        ip += 1
                        obj = stack[-1]
                        if type(obj) is Instance:
                            v = obj.fields.get(name, _MISSING)
                            if v is _MISSING:
                                v = self.get_attr(obj, name)
                            stack[-1] = v
                        else:
                            stack[-1] = self.get_attr(obj, name)
                    elif o == SET_ATTR:
                        name = consts[code[ip]]
                        ip += 1
                        value = pop()
                        obj = pop()
                        if type(obj) is not Instance:
                            raise RuntimeFault("TypeError", "cannot set attribute '%s' on %s"
                                               % (name, type_name(obj)))
                        obj.fields[name] = value
                        push(value)
                    elif o == GET_UPVALUE:
                        uv = upvals[code[ip]]
                        ip += 1
                        i = uv.index
                        push(stack[i] if i >= 0 else uv.value)
                    elif o == SET_UPVALUE:
                        uv = upvals[code[ip]]
                        ip += 1
                        if uv.index >= 0:
                            stack[uv.index] = stack[-1]
                        else:
                            uv.value = stack[-1]
                    elif o == GET_INDEX:
                        idx = pop()
                        obj = stack[-1]
                        if type(obj) is list and type(idx) is int and -len(obj) <= idx < len(obj):
                            stack[-1] = obj[idx]
                        else:
                            stack[-1] = get_index(obj, idx)
                    elif o == SET_INDEX:
                        value = pop()
                        idx = pop()
                        obj = pop()
                        set_index(obj, idx, value)
                        push(value)
                    elif o == MUL:
                        b = pop()
                        a = stack[-1]
                        if type(a) is int and type(b) is int:
                            stack[-1] = a * b
                        else:
                            stack[-1] = binary_op(MUL, a, b)
                    elif o == DIV or o == FLOORDIV or o == MOD or o == POW:
                        b = pop()
                        stack[-1] = binary_op(o, stack[-1], b)
                    elif o == EQ:
                        b = pop()
                        stack[-1] = values_equal(stack[-1], b)
                    elif o == NE:
                        b = pop()
                        stack[-1] = not values_equal(stack[-1], b)
                    elif o == LE or o == GT or o == GE:
                        b = pop()
                        stack[-1] = compare_op(o, stack[-1], b)
                    elif o == IN:
                        b = pop()
                        stack[-1] = contains(stack[-1], b)
                    elif o == NOT:
                        stack[-1] = not stack[-1]
                    elif o == NEG:
                        a = stack[-1]
                        if not _num(a):
                            raise RuntimeFault("TypeError", "bad operand type for unary -: %s"
                                               % type_name(a))
                        stack[-1] = -a
                    elif o == NULL:
                        push(None)
                    elif o == TRUE:
                        push(True)
                    elif o == FALSE:
                        push(False)
                    elif o == JUMP_IF_TRUE:
                        if pop():
                            ip = code[ip]
                        else:
                            ip += 1
                    elif o == JUMP_IF_FALSE_OR_POP:
                        if stack[-1]:
                            pop()
                            ip += 1
                        else:
                            ip = code[ip]
                    elif o == JUMP_IF_TRUE_OR_POP:
                        if stack[-1]:
                            ip = code[ip]
                        else:
                            pop()
                            ip += 1
                    elif o == FOR_ITER:
                        v = next(stack[-1], _DONE)
                        if v is _DONE:
                            ip = code[ip]
                        else:
                            push(v)
                            ip += 1
                    elif o == GET_ITER:
                        obj = stack[-1]
                        t = type(obj)
                        if t is list or t is str:
                            stack[-1] = iter(obj)
                        elif t is dict:
                            stack[-1] = iter(list(obj))
                        else:
                            raise RuntimeFault("TypeError", "%s is not iterable" % type_name(obj))
                    elif o == SET_GLOBAL or o == STORE_GLOBAL:
                        name = consts[code[ip]]
                        ip += 1
                        if name not in globals_:
                            raise RuntimeFault("NameError",
                                               "cannot assign to undeclared variable '%s' "
                                               "(declare it with 'let')" % name)
                        globals_[name] = stack[-1]
                        if o == STORE_GLOBAL:
                            pop()
                    elif o == DEFINE_GLOBAL:
                        globals_[consts[code[ip]]] = pop()
                        ip += 1
                    elif o == CLOSURE:
                        proto = consts[code[ip]]
                        ip += 1
                        ups = []
                        for is_local, index in proto.upvalue_specs:
                            if is_local:
                                ups.append(self.capture(base + index))
                            else:
                                ups.append(upvals[index])
                        push(Closure(proto, ups, globals_))
                    elif o == CLOSE_UPVALUE:
                        self.close_upvalues(len(stack) - 1)
                        pop()
                    elif o == DUP:
                        push(stack[-1])
                    elif o == DUP2:
                        push(stack[-2])
                        push(stack[-2])
                    elif o == BUILD_LIST:
                        n = code[ip]
                        ip += 1
                        if n:
                            items = stack[-n:]
                            del stack[-n:]
                        else:
                            items = []
                        push(items)
                    elif o == BUILD_DICT:
                        n = code[ip] * 2
                        ip += 1
                        d = {}
                        if n:
                            items = stack[-n:]
                            del stack[-n:]
                            for i in range(0, n, 2):
                                d[check_hashable(items[i])] = items[i + 1]
                        push(d)
                    elif o == SETUP_TRY:
                        if frame.handlers is None:
                            frame.handlers = []
                        frame.handlers.append((code[ip], len(stack)))
                        ip += 1
                    elif o == POP_TRY:
                        frame.handlers.pop()
                    elif o == THROW:
                        value = pop()
                        frame.ip = ip
                        trace = None
                        if type(value) is Instance and value.trace is not None:
                            trace = value.trace          # rethrow keeps original trace
                        raise TernThrow(value, trace)
                    elif o == SUPER_INVOKE:
                        name = consts[code[ip]]
                        n = code[ip + 1]
                        ip += 2
                        superclass = pop()
                        method = superclass.methods.get(name)
                        if method is None:
                            raise RuntimeFault("AttributeError", "superclass %s has no method '%s'"
                                               % (superclass.name, name))
                        frame.ip = ip
                        self.push_frame(method, n)
                        frame = frames[-1]
                        code = frame.code
                        consts = frame.consts
                        ip = 0
                        base = frame.base
                        upvals = frame.upvalues
                        globals_ = frame.globals
                    elif o == GET_SUPER:
                        name = consts[code[ip]]
                        ip += 1
                        superclass = pop()
                        method = superclass.methods.get(name)
                        if method is None:
                            raise RuntimeFault("AttributeError", "superclass %s has no method '%s'"
                                               % (superclass.name, name))
                        stack[-1] = BoundMethod(stack[-1], method)
                    elif o == CLASS:
                        push(TernClass(consts[code[ip]]))
                        ip += 1
                    elif o == INHERIT:
                        cls = pop()
                        sup = stack[-1]
                        if type(sup) is not TernClass:
                            raise RuntimeFault("TypeError", "superclass must be a class, got %s"
                                               % type_name(sup))
                        cls.methods.update(sup.methods)
                        cls.superclass = sup
                    elif o == METHOD:
                        method = pop()
                        stack[-1].methods[consts[code[ip]]] = method
                        ip += 1
                    elif o == IMPORT:
                        path = consts[code[ip]]
                        ip += 1
                        frame.ip = ip
                        push(self.import_module(path, frame.closure.proto.filename))
                    else:
                        raise RuntimeFault("InternalError", "unknown opcode %r" % o)
            except TernThrow as e:
                value, trace = e.value, e.trace
            except RuntimeFault as e:
                value, trace = self.make_error(e.kind, e.message), None
            except TernSyntaxError as e:
                value, trace = self.make_error("SyntaxError", e.format()), None
            except RecursionError:
                value, trace = self.make_error(
                    "StackOverflowError", "maximum recursion depth exceeded in native call"), None
            except (OSError, ValueError, TypeError, AttributeError, IndexError, KeyError,
                    ArithmeticError, MemoryError) as e:
                value, trace = self.make_error("InternalError", "%s: %s" % (type(e).__name__, e)), None
            frame.ip = ip
            if trace is None:
                trace = self.capture_trace()
            if type(value) is Instance and value.trace is None:
                value.trace = trace
            frame = self.unwind(value, trace, stop_depth)
            code = frame.code
            consts = frame.consts
            ip = frame.ip
            base = frame.base
            upvals = frame.upvalues
            globals_ = frame.globals
