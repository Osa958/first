"""Single-pass AST -> bytecode compiler.

The compiler emits a list of `Instr` objects interleaved with `Label`
markers.  After a function is compiled, the (optional) peephole optimizer
rewrites that list, and the assembler lays it out as a flat list of ints,
resolving labels to absolute offsets.

Variables
---------
* Top-level `let`/`fn`/`class` declarations are module globals (stored in a
  per-module dict, looked up by name at runtime, so mutual recursion works).
* Everything else is a local living in a VM stack slot.  Slot 0 of every
  frame holds the callee (or `self` in methods).
* A local referenced by an inner function is marked `captured`; when it
  goes out of scope the compiler emits CLOSE_UPVALUE instead of POP so the
  VM migrates it to the heap (see objects.Upvalue).

Invariant: at every statement boundary the VM stack (above the frame base)
holds exactly the declared locals.  Exception handlers rely on this.
"""
from . import nodes as N
from . import opcodes as op
from .errors import TernCompileError
from .objects import FunctionProto
from .optimizer import optimize


class Label:
    __slots__ = ("offset", "name")
    _counter = 0

    def __init__(self):
        Label._counter += 1
        self.name = "L%d" % Label._counter
        self.offset = None

    def __repr__(self):
        return self.name


class Instr:
    __slots__ = ("op", "args", "pos")

    def __init__(self, opcode, args, pos):
        self.op = opcode
        self.args = list(args)
        self.pos = pos

    def __repr__(self):
        return "%s %s" % (op.NAMES[self.op], self.args)


class Local:
    __slots__ = ("name", "depth", "captured")

    def __init__(self, name, depth):
        self.name = name
        self.depth = depth          # -1 while the initializer runs
        self.captured = False


class LoopCtx:
    def __init__(self, continue_label, break_label, local_count):
        self.continue_label = continue_label
        self.break_label = break_label
        self.local_count = local_count


class TryCtx:
    def __init__(self, finally_body):
        self.finally_body = finally_body


class ClassCtx:
    def __init__(self, has_super):
        self.has_super = has_super


class FnState:
    def __init__(self, enclosing, proto, kind):
        self.enclosing = enclosing
        self.proto = proto
        self.kind = kind
        self.code = []
        self.locals = [Local("self" if kind in ("method", "init") else "", 0)]
        self.upvalues = []
        self.scope_depth = 0
        self.control = []           # LoopCtx / TryCtx stack
        self.const_index = {}


BINOPS = {"+": op.ADD, "-": op.SUB, "*": op.MUL, "/": op.DIV, "//": op.FLOORDIV,
          "%": op.MOD, "**": op.POW, "==": op.EQ, "!=": op.NE, "<": op.LT,
          "<=": op.LE, ">": op.GT, ">=": op.GE, "in": op.IN}

MAX_LOCALS = 65535


class Compiler:
    def __init__(self, filename="<input>", source="", optimize=True):
        self.filename = filename
        self.source = source
        self.optimize = optimize
        self.fs = None
        self.classes = []

    # ---- helpers ---------------------------------------------------------
    def error(self, msg, node):
        raise TernCompileError(msg, node.line, node.col, self.filename, self.source)

    def emit(self, opcode, *args, at=None):
        pos = (at.line, at.col) if at is not None else (0, 0)
        self.fs.code.append(Instr(opcode, args, pos))

    def mark(self, label):
        self.fs.code.append(label)

    def const(self, value):
        fs = self.fs
        if isinstance(value, FunctionProto):
            fs.proto.consts.append(value)
            return len(fs.proto.consts) - 1
        key = (type(value), repr(value))
        idx = fs.const_index.get(key)
        if idx is None:
            fs.proto.consts.append(value)
            idx = len(fs.proto.consts) - 1
            fs.const_index[key] = idx
        return idx

    # ---- scopes and variables ---------------------------------------------
    def begin_scope(self):
        self.fs.scope_depth += 1

    def end_scope(self, at=None, emit=True):
        fs = self.fs
        fs.scope_depth -= 1
        while fs.locals and fs.locals[-1].depth > fs.scope_depth:
            local = fs.locals.pop()
            if emit:
                self.emit(op.CLOSE_UPVALUE if local.captured else op.POP, at=at)

    def pop_locals_to(self, count, at):
        """Emit pops for locals above `count` without forgetting them."""
        for local in reversed(self.fs.locals[count:]):
            self.emit(op.CLOSE_UPVALUE if local.captured else op.POP, at=at)

    def add_local(self, name, node, initialized=False):
        fs = self.fs
        if len(fs.locals) >= MAX_LOCALS:
            self.error("Too many local variables in function", node)
        local = Local(name, fs.scope_depth if initialized else -1)
        fs.locals.append(local)
        return len(fs.locals) - 1

    def declare(self, name, node):
        """Declare a variable; returns True if it is global."""
        if self.fs.scope_depth == 0:
            return True
        self.check_redeclare(name, node)
        self.add_local(name, node)
        return False

    def check_redeclare(self, name, node):
        fs = self.fs
        for local in reversed(fs.locals):
            if local.depth != -1 and local.depth < fs.scope_depth:
                break
            if local.name == name:
                self.error("Variable '%s' is already declared in this scope" % name, node)

    def define(self, name, is_global, node):
        if is_global:
            self.emit(op.DEFINE_GLOBAL, self.const(name), at=node)
        else:
            self.fs.locals[-1].depth = self.fs.scope_depth

    def resolve_local(self, fs, name, node):
        for i in range(len(fs.locals) - 1, -1, -1):
            local = fs.locals[i]
            if local.name == name:
                if local.depth == -1:
                    self.error("Cannot read local variable '%s' in its own initializer" % name, node)
                return i
        return -1

    def add_upvalue(self, fs, is_local, index):
        spec = (is_local, index)
        if spec in fs.upvalues:
            return fs.upvalues.index(spec)
        fs.upvalues.append(spec)
        return len(fs.upvalues) - 1

    def resolve_upvalue(self, fs, name, node):
        if fs.enclosing is None:
            return -1
        local = self.resolve_local(fs.enclosing, name, node)
        if local != -1:
            fs.enclosing.locals[local].captured = True
            return self.add_upvalue(fs, True, local)
        up = self.resolve_upvalue(fs.enclosing, name, node)
        if up != -1:
            return self.add_upvalue(fs, False, up)
        return -1

    def load_var(self, name, node):
        slot = self.resolve_local(self.fs, name, node)
        if slot != -1:
            self.emit(op.GET_LOCAL, slot, at=node)
            return
        up = self.resolve_upvalue(self.fs, name, node)
        if up != -1:
            self.emit(op.GET_UPVALUE, up, at=node)
            return
        self.emit(op.GET_GLOBAL, self.const(name), at=node)

    def store_var(self, name, node):
        slot = self.resolve_local(self.fs, name, node)
        if slot != -1:
            self.emit(op.SET_LOCAL, slot, at=node)
            return
        up = self.resolve_upvalue(self.fs, name, node)
        if up != -1:
            self.emit(op.SET_UPVALUE, up, at=node)
            return
        self.emit(op.SET_GLOBAL, self.const(name), at=node)

    # ---- entry points --------------------------------------------------------
    def compile_module(self, stmts, repl=False):
        proto = FunctionProto("<module>", 0, "script", self.filename, self.source, 1)
        self.fs = FnState(None, proto, "script")
        last = stmts[-1] if stmts else None
        for stmt in stmts:
            if repl and stmt is last and isinstance(stmt, N.ExprStmt):
                self.expr(stmt.expr)
                self.emit(op.RETURN, at=stmt)
                break
            self.stmt(stmt)
        else:
            self.emit(op.NULL, at=last)
            self.emit(op.RETURN, at=last)
        return self.finish()

    def finish(self):
        fs = self.fs
        code = fs.code
        if self.optimize:
            code = optimize(code, fs.proto.consts, self.const)
        assemble(fs.proto, code)
        fs.proto.upvalue_specs = list(fs.upvalues)
        return fs.proto

    def function(self, name, params, body, kind, node):
        proto = FunctionProto(name, len(params), kind, self.filename, self.source, node.line)
        fs = FnState(self.fs, proto, kind)
        self.fs = fs
        fs.scope_depth = 1
        for p in params:
            self.add_local(p, node, initialized=True)
        for stmt in body:
            self.stmt(stmt)
        if kind == "init":
            self.emit(op.GET_LOCAL, 0, at=node)
        else:
            self.emit(op.NULL, at=node)
        self.emit(op.RETURN, at=node)
        self.finish()
        self.fs = fs.enclosing
        self.emit(op.CLOSURE, self.const(proto), at=node)

    # ---- statements ----------------------------------------------------------
    def stmt(self, node):
        getattr(self, "stmt_" + type(node).__name__)(node)

    def block(self, stmts, at=None):
        self.begin_scope()
        for s in stmts:
            self.stmt(s)
        self.end_scope(at=at)

    def stmt_Block(self, node):
        self.block(node.body, at=node)

    def stmt_ExprStmt(self, node):
        self.expr(node.expr)
        self.emit(op.POP, at=node)

    def stmt_Let(self, node):
        # The initializer is compiled *before* the new variable is declared,
        # so `let x = x + 1` in an inner scope reads the outer `x` (shadowing).
        is_global = self.fs.scope_depth == 0
        if not is_global:
            self.check_redeclare(node.name, node)
        if node.value is None:
            self.emit(op.NULL, at=node)
        else:
            self.expr(node.value)
        if not is_global:
            self.add_local(node.name, node)
        self.define(node.name, is_global, node)

    def stmt_FnDecl(self, node):
        is_global = self.declare(node.name, node)
        if not is_global:
            self.fs.locals[-1].depth = self.fs.scope_depth   # allow recursion
        self.function(node.name, node.params, node.body, "function", node)
        self.define(node.name, is_global, node)

    def stmt_ClassDecl(self, node):
        is_global = self.declare(node.name, node)
        self.emit(op.CLASS, self.const(node.name), at=node)
        self.define(node.name, is_global, node)
        ctx = ClassCtx(node.superclass is not None)
        self.classes.append(ctx)
        if node.superclass is not None:
            if isinstance(node.superclass, N.Ident) and node.superclass.name == node.name:
                self.error("A class cannot inherit from itself", node.superclass)
            self.begin_scope()
            self.expr(node.superclass)
            self.add_local("super", node, initialized=True)
            self.load_var(node.name, node)
            self.emit(op.INHERIT, at=node.superclass)
        self.load_var(node.name, node)
        seen = set()
        for m in node.methods:
            if m.name in seen:
                self.error("Method '%s' is defined twice in class '%s'" % (m.name, node.name), m)
            seen.add(m.name)
            kind = "init" if m.name == "init" else "method"
            self.function(m.name, m.params, m.body, kind, m)
            self.emit(op.METHOD, self.const(m.name), at=m)
        self.emit(op.POP, at=node)
        if node.superclass is not None:
            self.end_scope(at=node)
        self.classes.pop()

    def stmt_If(self, node):
        self.expr(node.cond)
        else_label = Label()
        self.emit(op.JUMP_IF_FALSE, else_label, at=node)
        self.block(node.then, at=node)
        if node.else_ is not None:
            end = Label()
            self.emit(op.JUMP, end, at=node)
            self.mark(else_label)
            self.block(node.else_, at=node)
            self.mark(end)
        else:
            self.mark(else_label)

    def stmt_While(self, node):
        start, end = Label(), Label()
        self.mark(start)
        self.expr(node.cond)
        self.emit(op.JUMP_IF_FALSE, end, at=node)
        self.fs.control.append(LoopCtx(start, end, len(self.fs.locals)))
        self.block(node.body, at=node)
        self.fs.control.pop()
        self.emit(op.JUMP, start, at=node)
        self.mark(end)

    def stmt_ForIn(self, node):
        self.expr(node.iterable)
        self.emit(op.GET_ITER, at=node)
        self.begin_scope()
        self.add_local("$iter", node, initialized=True)
        start, end = Label(), Label()
        self.mark(start)
        self.emit(op.FOR_ITER, end, at=node)
        self.fs.control.append(LoopCtx(start, end, len(self.fs.locals)))
        self.begin_scope()
        self.add_local(node.var, node, initialized=True)
        self.block(node.body, at=node)
        self.end_scope(at=node)
        self.fs.control.pop()
        self.emit(op.JUMP, start, at=node)
        self.mark(end)
        self.end_scope(at=node)

    def unwind_trys(self, stop_index, node):
        """Run POP_TRY + finally blocks of TryCtx entries above stop_index."""
        fs = self.fs
        for i in range(len(fs.control) - 1, stop_index, -1):
            ctx = fs.control[i]
            if isinstance(ctx, TryCtx):
                self.emit(op.POP_TRY, at=node)
                if ctx.finally_body is not None:
                    saved = fs.control
                    fs.control = saved[:i]
                    self.block(ctx.finally_body, at=node)
                    fs.control = saved

    def find_loop(self, node, what):
        for i in range(len(self.fs.control) - 1, -1, -1):
            if isinstance(self.fs.control[i], LoopCtx):
                return i
        self.error("'%s' outside of a loop" % what, node)

    def stmt_Break(self, node):
        i = self.find_loop(node, "break")
        loop = self.fs.control[i]
        self.unwind_trys(i, node)
        self.pop_locals_to(loop.local_count, node)
        self.emit(op.JUMP, loop.break_label, at=node)

    def stmt_Continue(self, node):
        i = self.find_loop(node, "continue")
        loop = self.fs.control[i]
        self.unwind_trys(i, node)
        self.pop_locals_to(loop.local_count, node)
        self.emit(op.JUMP, loop.continue_label, at=node)

    def stmt_Return(self, node):
        fs = self.fs
        if fs.kind == "script":
            self.error("'return' outside of a function", node)
        if fs.kind == "init":
            if node.value is not None:
                self.error("Cannot return a value from an initializer", node)
            self.emit(op.GET_LOCAL, 0, at=node)
        elif node.value is None:
            self.emit(op.NULL, at=node)
        else:
            self.expr(node.value)
        if any(isinstance(c, TryCtx) for c in fs.control):
            # keep the return value in a hidden local while finally blocks run
            self.begin_scope()
            slot = self.add_local("$ret", node, initialized=True)
            self.unwind_trys(-1, node)
            self.emit(op.GET_LOCAL, slot, at=node)
            self.emit(op.RETURN, at=node)
            self.end_scope(emit=False)
        else:
            self.emit(op.RETURN, at=node)

    def stmt_Throw(self, node):
        self.expr(node.value)
        self.emit(op.THROW, at=node)

    def stmt_Try(self, node):
        fs = self.fs
        F = node.finally_body
        handler, end = Label(), Label()
        self.emit(op.SETUP_TRY, handler, at=node)
        fs.control.append(TryCtx(F))
        self.block(node.body, at=node)
        fs.control.pop()
        self.emit(op.POP_TRY, at=node)
        if F is not None:
            self.block(F, at=node)
        self.emit(op.JUMP, end, at=node)

        self.mark(handler)                       # stack: [..locals, exception]
        if node.catch_body is not None:
            self.begin_scope()
            self.add_local(node.catch_var or "$exc", node, initialized=True)
            handler2 = Label()
            if F is not None:
                self.emit(op.SETUP_TRY, handler2, at=node)
                fs.control.append(TryCtx(F))
            self.block(node.catch_body, at=node)
            if F is not None:
                fs.control.pop()
                self.emit(op.POP_TRY, at=node)
            self.end_scope(at=node)
            if F is not None:
                self.block(F, at=node)
                self.emit(op.JUMP, end, at=node)
                # exception escaped the catch block: run finally, rethrow
                self.mark(handler2)              # stack: [..locals, caught, exc2]
                self.begin_scope()
                self.add_local("$caught", node, initialized=True)
                slot = self.add_local("$exc", node, initialized=True)
                self.block(F, at=node)
                self.emit(op.GET_LOCAL, slot, at=node)
                self.emit(op.THROW, at=node)
                self.end_scope(emit=False)
        else:
            self.begin_scope()
            slot = self.add_local("$exc", node, initialized=True)
            self.block(F, at=node)
            self.emit(op.GET_LOCAL, slot, at=node)
            self.emit(op.THROW, at=node)
            self.end_scope(emit=False)
        self.mark(end)

    def stmt_Import(self, node):
        alias = node.alias or node.path.replace("\\", "/").split("/")[-1]
        if alias.endswith(".tern"):
            alias = alias[:-5]
        if not alias.isidentifier():
            self.error("Cannot derive a variable name from import path '%s'; use 'as name'"
                       % node.path, node)
        is_global = self.declare(alias, node)
        self.emit(op.IMPORT, self.const(node.path), at=node)
        self.define(alias, is_global, node)

    # ---- expressions -----------------------------------------------------------
    def expr(self, node):
        getattr(self, "expr_" + type(node).__name__)(node)

    def expr_Literal(self, node):
        v = node.value
        if v is None:
            self.emit(op.NULL, at=node)
        elif v is True:
            self.emit(op.TRUE, at=node)
        elif v is False:
            self.emit(op.FALSE, at=node)
        else:
            self.emit(op.CONST, self.const(v), at=node)

    def expr_Ident(self, node):
        self.load_var(node.name, node)

    def expr_SelfExpr(self, node):
        if not self.classes:
            self.error("Cannot use 'self' outside of a class method", node)
        self.load_var("self", node)

    def expr_SuperAttr(self, node):
        self.check_super(node)
        self.load_var("self", node)
        self.load_var("super", node)
        self.emit(op.GET_SUPER, self.const(node.name), at=node)

    def check_super(self, node):
        if not self.classes:
            self.error("Cannot use 'super' outside of a class method", node)
        if not self.classes[-1].has_super:
            self.error("Cannot use 'super' in a class with no superclass", node)

    def expr_ListLit(self, node):
        for item in node.items:
            self.expr(item)
        self.emit(op.BUILD_LIST, len(node.items), at=node)

    def expr_DictLit(self, node):
        for k, v in node.pairs:
            self.expr(k)
            self.expr(v)
        self.emit(op.BUILD_DICT, len(node.pairs), at=node)

    def expr_Unary(self, node):
        self.expr(node.operand)
        self.emit(op.NEG if node.op == "-" else op.NOT, at=node)

    def expr_Binary(self, node):
        self.expr(node.left)
        self.expr(node.right)
        self.emit(BINOPS[node.op], at=node)

    def expr_Logical(self, node):
        self.expr(node.left)
        end = Label()
        self.emit(op.JUMP_IF_FALSE_OR_POP if node.op == "and" else op.JUMP_IF_TRUE_OR_POP,
                  end, at=node)
        self.expr(node.right)
        self.mark(end)

    def expr_Assign(self, node):
        target = node.target
        if isinstance(target, N.Ident):
            if node.op is not None:
                self.load_var(target.name, target)
                self.expr(node.value)
                self.emit(BINOPS[node.op], at=node)
            else:
                self.expr(node.value)
            self.store_var(target.name, target)
        elif isinstance(target, N.Attr):
            self.expr(target.obj)
            name = self.const(target.name)
            if node.op is not None:
                self.emit(op.DUP, at=node)
                self.emit(op.GET_ATTR, name, at=target)
                self.expr(node.value)
                self.emit(BINOPS[node.op], at=node)
            else:
                self.expr(node.value)
            self.emit(op.SET_ATTR, name, at=target)
        else:  # Index
            self.expr(target.obj)
            self.expr(target.index)
            if node.op is not None:
                self.emit(op.DUP2, at=node)
                self.emit(op.GET_INDEX, at=target)
                self.expr(node.value)
                self.emit(BINOPS[node.op], at=node)
            else:
                self.expr(node.value)
            self.emit(op.SET_INDEX, at=target)

    def expr_Call(self, node):
        callee = node.callee
        if len(node.args) > 255:
            self.error("Too many arguments (max 255)", node)
        if isinstance(callee, N.Attr):
            self.expr(callee.obj)
            for a in node.args:
                self.expr(a)
            self.emit(op.INVOKE, self.const(callee.name), len(node.args), at=node)
        elif isinstance(callee, N.SuperAttr):
            self.check_super(callee)
            self.load_var("self", callee)
            for a in node.args:
                self.expr(a)
            self.load_var("super", callee)
            self.emit(op.SUPER_INVOKE, self.const(callee.name), len(node.args), at=node)
        else:
            self.expr(callee)
            for a in node.args:
                self.expr(a)
            self.emit(op.CALL, len(node.args), at=node)

    def expr_Attr(self, node):
        self.expr(node.obj)
        self.emit(op.GET_ATTR, self.const(node.name), at=node)

    def expr_Index(self, node):
        self.expr(node.obj)
        self.expr(node.index)
        self.emit(op.GET_INDEX, at=node)

    def expr_FnExpr(self, node):
        self.function(node.name or "<lambda>", node.params, node.body, "function", node)


def assemble(proto, items):
    pc = 0
    for item in items:
        if isinstance(item, Label):
            item.offset = pc
        else:
            pc += 1 + len(item.args)
    code = []
    positions = []
    for item in items:
        if isinstance(item, Label):
            continue
        code.append(item.op)
        for a in item.args:
            code.append(a.offset if isinstance(a, Label) else a)
        positions.extend([item.pos] * (1 + len(item.args)))
    proto.code = code
    proto.positions = positions


def compile_source(source, filename="<input>", optimize=True, repl=False):
    from .parser import parse
    stmts = parse(source, filename)
    return Compiler(filename, source, optimize).compile_module(stmts, repl=repl)
