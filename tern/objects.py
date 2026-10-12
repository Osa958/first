"""Runtime object model and value helpers.

Tern values map onto Python values where possible:
    int -> int, float -> float, string -> str, bool -> bool, null -> None,
    list -> list, dict -> dict.
Functions, classes, instances, modules, ... use the classes below.
"""
from .errors import RuntimeFault


class FunctionProto:
    """Compiled function: bytecode + constants + metadata. Immutable."""
    __slots__ = ("name", "arity", "code", "consts", "positions", "upvalue_specs",
                 "filename", "source", "kind", "line", "module")

    def __init__(self, name, arity, kind, filename, source, line):
        self.name = name
        self.arity = arity
        self.kind = kind            # "script" | "function" | "method" | "init"
        self.filename = filename
        self.source = source
        self.line = line
        self.code = []
        self.consts = []
        self.positions = []         # (line, col) for every code slot
        self.upvalue_specs = []     # [(is_local, index)]
        self.module = None          # set by the loader

    def __repr__(self):
        return "<proto %s>" % self.name


class Upvalue:
    """A captured variable.

    While the variable's frame is alive the upvalue is *open*: `index` is the
    absolute stack slot holding the value.  When the slot goes out of scope
    (block exit, return, or exception unwinding) the VM *closes* it: the
    value is copied into `value` and `index` becomes -1.  All closures that
    captured the variable share this one object, so they keep seeing the
    same (now heap-allocated) variable.
    """
    __slots__ = ("index", "value")

    def __init__(self, index):
        self.index = index
        self.value = None


class Closure:
    __slots__ = ("proto", "upvalues", "globals")

    def __init__(self, proto, upvalues, globals_):
        self.proto = proto
        self.upvalues = upvalues
        self.globals = globals_


class NativeFunction:
    __slots__ = ("name", "fn", "min_args", "max_args")

    def __init__(self, name, fn, min_args, max_args):
        self.name = name
        self.fn = fn              # fn(vm, args) -> value
        self.min_args = min_args
        self.max_args = max_args  # None = variadic


class NativeMethod:
    """A built-in method bound to a receiver, e.g. "abc".upper"""
    __slots__ = ("name", "fn", "receiver", "min_args", "max_args")

    def __init__(self, name, fn, receiver, min_args, max_args):
        self.name = name
        self.fn = fn              # fn(vm, receiver, args)
        self.receiver = receiver
        self.min_args = min_args
        self.max_args = max_args


class TernClass:
    __slots__ = ("name", "methods", "superclass")

    def __init__(self, name):
        self.name = name
        self.methods = {}
        self.superclass = None

    def is_subclass_of(self, other):
        cls = self
        while cls is not None:
            if cls is other:
                return True
            cls = cls.superclass
        return False


class Instance:
    __slots__ = ("cls", "fields", "trace")

    def __init__(self, cls):
        self.cls = cls
        self.fields = {}
        self.trace = None         # traceback captured when thrown


class BoundMethod:
    __slots__ = ("receiver", "method")

    def __init__(self, receiver, method):
        self.receiver = receiver
        self.method = method      # Closure


class Module:
    __slots__ = ("name", "path", "globals")

    def __init__(self, name, path, globals_):
        self.name = name
        self.path = path
        self.globals = globals_


# ---------------------------------------------------------------------------
# value helpers
# ---------------------------------------------------------------------------

NUMBER_TYPES = (int, float)


def type_name(v):
    t = type(v)
    if v is None:
        return "null"
    if t is bool:
        return "bool"
    if t is int:
        return "int"
    if t is float:
        return "float"
    if t is str:
        return "string"
    if t is list:
        return "list"
    if t is dict:
        return "dict"
    if t is Closure or t is NativeFunction or t is NativeMethod or t is BoundMethod:
        return "function"
    if t is TernClass:
        return "class"
    if t is Instance:
        return v.cls.name
    if t is Module:
        return "module"
    return "object"


def is_number(v):
    t = type(v)
    return t is int or t is float


def values_equal(a, b):
    if a is b:
        return True
    ta = type(a)
    tb = type(b)
    if ta is bool or tb is bool:
        return False            # bools are singletons; 1 != true
    if ta is list and tb is list:
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b))
    if ta is dict and tb is dict:
        if len(a) != len(b):
            return False
        for k, v in a.items():
            if k not in b or not values_equal(v, b[k]):
                return False
        return True
    if ta in NUMBER_TYPES and tb in NUMBER_TYPES:
        return a == b
    if ta is not tb:
        return False
    if ta is str:
        return a == b
    return False                # instances, functions: identity only


def format_float(f):
    if f != f:
        return "nan"
    if f in (float("inf"), float("-inf")):
        return "inf" if f > 0 else "-inf"
    r = repr(f)
    return r


def to_str(v, vm=None, quote=False, _seen=None):
    """Tern's string conversion. `quote` is used for strings inside containers."""
    t = type(v)
    if t is str:
        return quote_string(v) if quote else v
    if v is None:
        return "null"
    if t is bool:
        return "true" if v else "false"
    if t is int:
        return str(v)
    if t is float:
        return format_float(v)
    if t is list or t is dict:
        _seen = _seen or set()
        if id(v) in _seen:
            return "[...]" if t is list else "{...}"
        _seen.add(id(v))
        if t is list:
            out = "[" + ", ".join(to_str(x, vm, True, _seen) for x in v) + "]"
        else:
            out = "{" + ", ".join("%s: %s" % (to_str(k, vm, True, _seen), to_str(x, vm, True, _seen))
                                  for k, x in v.items()) + "}"
        _seen.discard(id(v))
        return out
    if t is Closure:
        return "<fn %s>" % v.proto.name
    if t is NativeFunction:
        return "<native fn %s>" % v.name
    if t is NativeMethod:
        return "<native method %s>" % v.name
    if t is BoundMethod:
        return "<bound method %s.%s>" % (v.receiver.cls.name, v.method.proto.name)
    if t is TernClass:
        return "<class %s>" % v.name
    if t is Instance:
        if vm is not None:
            method = v.cls.methods.get("to_string")
            if method is not None:
                result = vm.call_value(BoundMethod(v, method), [])
                if type(result) is not str:
                    raise RuntimeFault("TypeError", "to_string() must return a string, got %s"
                                       % type_name(result))
                return result
        return "<%s instance>" % v.cls.name
    if t is Module:
        return "<module %s>" % v.name
    return "<%s>" % t.__name__


def quote_string(s):
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def check_hashable(key):
    t = type(key)
    if t is list or t is dict:
        raise RuntimeFault("TypeError", "%s cannot be used as a dictionary key" % type_name(key))
    if t is bool:
        # Python would merge true/1 and false/0 into one key, but in Tern
        # 1 != true, so bool keys are rejected rather than silently colliding.
        raise RuntimeFault("TypeError", "bool cannot be used as a dictionary key")
    return key
