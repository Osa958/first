"""Built-in functions, built-in type methods and the Tern prelude."""
import functools
import math
import os
import time

from .errors import RuntimeFault
from .objects import (Instance, NativeFunction, TernClass, check_hashable, to_str,
                      type_name, values_equal)

# Error hierarchy, written in Tern itself and loaded into the builtins.
PRELUDE = """
class Error {
    fn init(message) { self.message = message }
    fn to_string() { return type(self) + ": " + str(self.message) }
}
class TypeError < Error {}
class NameError < Error {}
class ValueError < Error {}
class IndexError < Error {}
class KeyError < Error {}
class AttributeError < Error {}
class ZeroDivisionError < Error {}
class IOError < Error {}
class ImportError < Error {}
class SyntaxError < Error {}
class AssertionError < Error {}
class StackOverflowError < Error {}
class InternalError < Error {}
"""


def fault(kind, msg):
    raise RuntimeFault(kind, msg)


def expect(value, types, fname, what="argument"):
    if type(value) not in types:
        names = " or ".join(sorted({_TYPE_NAMES[t] for t in types}))
        fault("TypeError", "%s() expects %s %s, got %s" % (fname, names, what, type_name(value)))
    return value


_TYPE_NAMES = {int: "int", float: "float", str: "string", list: "list", dict: "dict",
               bool: "bool", type(None): "null"}
NUM = (int, float)


def iterable(vm, value, fname):
    t = type(value)
    if t is list or t is str:
        return value
    if t is dict:
        return list(value)
    fault("TypeError", "%s() expects an iterable (list, string or dict), got %s"
          % (fname, type_name(value)))


def tern_sort_key(vm, key_fn=None):
    from .vm import compare_op
    from . import opcodes as O

    def cmp(a, b):
        if compare_op(O.LT, a, b):
            return -1
        if compare_op(O.LT, b, a):
            return 1
        return 0
    if key_fn is None:
        return functools.cmp_to_key(cmp)
    return lambda item: functools.cmp_to_key(cmp)(vm.call_value(key_fn, [item]))


def sort_list(vm, items, key_fn=None):
    if key_fn is None:
        return sorted(items, key=tern_sort_key(vm))
    keyed = [(vm.call_value(key_fn, [x]), x) for x in items]
    keyfunc = tern_sort_key(vm)
    keyed.sort(key=lambda pair: keyfunc(pair[0]))
    return [x for _, x in keyed]


# ---------------------------------------------------------------------------
# global functions: fn(vm, args)
# ---------------------------------------------------------------------------

def b_print(vm, args):
    vm.out.write(" ".join(to_str(a, vm) for a in args) + "\n")


def b_len(vm, args):
    v = args[0]
    if type(v) in (str, list, dict):
        return len(v)
    fault("TypeError", "len() expects a string, list or dict, got %s" % type_name(v))


def b_range(vm, args):
    for a in args:
        expect(a, (int,), "range")
    if len(args) == 1:
        return list(range(args[0]))
    step = args[2] if len(args) == 3 else 1
    if step == 0:
        fault("ValueError", "range() step must not be zero")
    return list(range(args[0], args[1], step))


def b_map(vm, args):
    fn, seq = args
    return [vm.call_value(fn, [x]) for x in iterable(vm, seq, "map")]


def b_filter(vm, args):
    fn, seq = args
    return [x for x in iterable(vm, seq, "filter") if vm.call_value(fn, [x])]


def b_reduce(vm, args):
    fn, seq = args[0], list(iterable(vm, args[1], "reduce"))
    if len(args) == 3:
        acc = args[2]
    elif seq:
        acc = seq.pop(0)
    else:
        fault("ValueError", "reduce() of empty sequence with no initial value")
    for x in seq:
        acc = vm.call_value(fn, [acc, x])
    return acc


def b_str(vm, args):
    return to_str(args[0], vm)


def b_repr(vm, args):
    return to_str(args[0], vm, quote=True)


def b_int(vm, args):
    v = args[0]
    t = type(v)
    if t is int:
        return v
    if t is float:
        if v != v or v in (float("inf"), float("-inf")):
            fault("ValueError", "cannot convert %s to int" % to_str(v))
        return int(v)
    if t is bool:
        return 1 if v else 0
    if t is str:
        try:
            return int(v.strip(), 10)
        except ValueError:
            fault("ValueError", "invalid int literal: %s" % to_str(v, quote=True))
    fault("TypeError", "int() cannot convert %s" % type_name(v))


def b_float(vm, args):
    v = args[0]
    t = type(v)
    if t is int or t is float:
        return float(v)
    if t is str:
        try:
            return float(v.strip())
        except ValueError:
            fault("ValueError", "invalid float literal: %s" % to_str(v, quote=True))
    fault("TypeError", "float() cannot convert %s" % type_name(v))


def b_bool(vm, args):
    return bool(args[0])


def b_type(vm, args):
    return type_name(args[0])


def b_isinstance(vm, args):
    v, cls = args
    if type(cls) is TernClass:
        return type(v) is Instance and v.cls.is_subclass_of(cls)
    if type(cls) is str:
        return type_name(v) == cls
    fault("TypeError", "isinstance() expects a class or a type name string")


def b_input(vm, args):
    if args:
        vm.out.write(to_str(args[0], vm))
        vm.out.flush()
    try:
        return input()
    except EOFError:
        return None


def b_read_file(vm, args):
    path = expect(args[0], (str,), "read_file", "path")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        fault("IOError", "cannot read '%s': %s" % (path, e.strerror or e))


def _write(path, content, mode, fname):
    expect(path, (str,), fname, "path")
    expect(content, (str,), fname, "content")
    try:
        with open(path, mode, encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        fault("IOError", "cannot write '%s': %s" % (path, e.strerror or e))


def b_write_file(vm, args):
    _write(args[0], args[1], "w", "write_file")


def b_append_file(vm, args):
    _write(args[0], args[1], "a", "append_file")


def b_file_exists(vm, args):
    return os.path.exists(expect(args[0], (str,), "file_exists", "path"))


def b_delete_file(vm, args):
    path = expect(args[0], (str,), "delete_file", "path")
    try:
        os.remove(path)
    except OSError as e:
        fault("IOError", "cannot delete '%s': %s" % (path, e.strerror or e))


def _numbers_or_list(args, fname):
    seq = args[0] if len(args) == 1 and type(args[0]) is list else args
    if not seq:
        fault("ValueError", "%s() of an empty sequence" % fname)
    return seq


def b_min(vm, args):
    seq = _numbers_or_list(args, "min")
    return sorted(seq, key=tern_sort_key(vm))[0]


def b_max(vm, args):
    seq = _numbers_or_list(args, "max")
    return sorted(seq, key=tern_sort_key(vm))[-1]


def b_sum(vm, args):
    total = 0
    for x in expect(args[0], (list,), "sum"):
        if type(x) not in NUM:
            fault("TypeError", "sum() expects a list of numbers, found %s" % type_name(x))
        total += x
    return total


def b_abs(vm, args):
    return abs(expect(args[0], NUM, "abs"))


def b_sqrt(vm, args):
    x = expect(args[0], NUM, "sqrt")
    if x < 0:
        fault("ValueError", "sqrt() of a negative number")
    return math.sqrt(x)


def b_floor(vm, args):
    return math.floor(expect(args[0], NUM, "floor"))


def b_ceil(vm, args):
    return math.ceil(expect(args[0], NUM, "ceil"))


def b_round(vm, args):
    x = expect(args[0], NUM, "round")
    if len(args) == 2:
        return round(x, expect(args[1], (int,), "round"))
    return int(round(x))


def b_sorted(vm, args):
    items = list(iterable(vm, args[0], "sorted"))
    return sort_list(vm, items, args[1] if len(args) > 1 else None)


def b_reversed(vm, args):
    return list(reversed(list(iterable(vm, args[0], "reversed"))))


def b_clock(vm, args):
    return time.perf_counter()


def b_chr(vm, args):
    code = expect(args[0], (int,), "chr")
    if not 0 <= code <= 0x10FFFF:
        fault("ValueError", "chr() argument out of range")
    return chr(code)


def b_ord(vm, args):
    s = expect(args[0], (str,), "ord")
    if len(s) != 1:
        fault("ValueError", "ord() expects a single character, got length %d" % len(s))
    return ord(s)


def b_assert(vm, args):
    if not args[0]:
        msg = to_str(args[1], vm) if len(args) > 1 else "assertion failed"
        raise RuntimeFault("AssertionError", msg)


def b_exit(vm, args):
    raise SystemExit(args[0] if args else 0)


def b_keys(vm, args):
    return list(expect(args[0], (dict,), "keys"))


def b_values(vm, args):
    return list(expect(args[0], (dict,), "values").values())


GLOBALS = {
    "print": (b_print, 0, None), "len": (b_len, 1, 1), "range": (b_range, 1, 3),
    "map": (b_map, 2, 2), "filter": (b_filter, 2, 2), "reduce": (b_reduce, 2, 3),
    "str": (b_str, 1, 1), "repr": (b_repr, 1, 1), "int": (b_int, 1, 1),
    "float": (b_float, 1, 1), "bool": (b_bool, 1, 1), "type": (b_type, 1, 1),
    "isinstance": (b_isinstance, 2, 2), "input": (b_input, 0, 1),
    "read_file": (b_read_file, 1, 1), "write_file": (b_write_file, 2, 2),
    "append_file": (b_append_file, 2, 2), "file_exists": (b_file_exists, 1, 1),
    "delete_file": (b_delete_file, 1, 1),
    "min": (b_min, 1, None), "max": (b_max, 1, None), "sum": (b_sum, 1, 1),
    "abs": (b_abs, 1, 1), "sqrt": (b_sqrt, 1, 1), "floor": (b_floor, 1, 1),
    "ceil": (b_ceil, 1, 1), "round": (b_round, 1, 2), "sorted": (b_sorted, 1, 2),
    "reversed": (b_reversed, 1, 1), "clock": (b_clock, 0, 0), "chr": (b_chr, 1, 1),
    "ord": (b_ord, 1, 1), "assert": (b_assert, 1, 2), "exit": (b_exit, 0, 1),
    "keys": (b_keys, 1, 1), "values": (b_values, 1, 1),
}


# ---------------------------------------------------------------------------
# methods on built-in types: fn(vm, receiver, args)
# ---------------------------------------------------------------------------

def _str_arg(v, fname):
    return expect(v, (str,), fname)


def _slice_bounds(n, args, fname):
    start = expect(args[0], (int,), fname) if args else 0
    end = expect(args[1], (int,), fname) if len(args) > 1 and args[1] is not None else n
    return start, end


def s_split(vm, s, args):
    if not args or args[0] is None:
        return s.split()
    sep = _str_arg(args[0], "split")
    if sep == "":
        fault("ValueError", "split() separator must not be empty")
    return s.split(sep)


def s_join(vm, s, args):
    items = expect(args[0], (list,), "join")
    for x in items:
        if type(x) is not str:
            fault("TypeError", "join() expects a list of strings, found %s" % type_name(x))
    return s.join(items)


def s_format(vm, s, args):
    out = []
    i = 0
    argi = 0
    while i < len(s):
        if s.startswith("{}", i):
            if argi >= len(args):
                fault("ValueError", "format() has more '{}' placeholders than arguments")
            out.append(to_str(args[argi], vm))
            argi += 1
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def s_repeat(vm, s, args):
    n = expect(args[0], (int,), "repeat")
    return s * max(n, 0)


def s_pad(left):
    def pad(vm, s, args):
        width = expect(args[0], (int,), "pad")
        ch = _str_arg(args[1], "pad") if len(args) > 1 else " "
        if len(ch) != 1:
            fault("ValueError", "pad character must be a single character")
        return s.rjust(width, ch) if left else s.ljust(width, ch)
    return pad


STRING_METHODS = {
    "len": (lambda vm, s, a: len(s), 0, 0),
    "upper": (lambda vm, s, a: s.upper(), 0, 0),
    "lower": (lambda vm, s, a: s.lower(), 0, 0),
    "strip": (lambda vm, s, a: s.strip(), 0, 0),
    "trim": (lambda vm, s, a: s.strip(), 0, 0),
    "split": (s_split, 0, 1),
    "join": (s_join, 1, 1),
    "replace": (lambda vm, s, a: s.replace(_str_arg(a[0], "replace"), _str_arg(a[1], "replace")), 2, 2),
    "find": (lambda vm, s, a: s.find(_str_arg(a[0], "find")), 1, 1),
    "index_of": (lambda vm, s, a: s.find(_str_arg(a[0], "index_of")), 1, 1),
    "count": (lambda vm, s, a: s.count(_str_arg(a[0], "count")), 1, 1),
    "contains": (lambda vm, s, a: _str_arg(a[0], "contains") in s, 1, 1),
    "starts_with": (lambda vm, s, a: s.startswith(_str_arg(a[0], "starts_with")), 1, 1),
    "ends_with": (lambda vm, s, a: s.endswith(_str_arg(a[0], "ends_with")), 1, 1),
    "slice": (lambda vm, s, a: s.__getitem__(slice(*_slice_bounds(len(s), a, "slice"))), 0, 2),
    "chars": (lambda vm, s, a: list(s), 0, 0),
    "repeat": (s_repeat, 1, 1),
    "format": (s_format, 0, None),
    "is_digit": (lambda vm, s, a: s.isdigit(), 0, 0),
    "is_alpha": (lambda vm, s, a: s.isalpha(), 0, 0),
    "is_space": (lambda vm, s, a: s.isspace(), 0, 0),
    "pad_left": (s_pad(True), 1, 2),
    "pad_right": (s_pad(False), 1, 2),
}


def l_pop(vm, lst, args):
    if not lst:
        fault("IndexError", "pop from empty list")
    if args:
        from .vm import check_index
        return lst.pop(check_index(lst, args[0]))
    return lst.pop()


def l_insert(vm, lst, args):
    idx = expect(args[0], (int,), "insert")
    lst.insert(idx, args[1])


def l_remove_at(vm, lst, args):
    from .vm import check_index
    return lst.pop(check_index(lst, args[0]))


def l_remove(vm, lst, args):
    for i, x in enumerate(lst):
        if values_equal(x, args[0]):
            del lst[i]
            return True
    return False


def l_index_of(vm, lst, args):
    for i, x in enumerate(lst):
        if values_equal(x, args[0]):
            return i
    return -1


def l_sort(vm, lst, args):
    lst[:] = sort_list(vm, lst, args[0] if args else None)


def l_reverse(vm, lst, args):
    lst.reverse()


def l_extend(vm, lst, args):
    lst.extend(expect(args[0], (list,), "extend"))


def l_join(vm, lst, args):
    sep = _str_arg(args[0], "join") if args else ""
    return sep.join(to_str(x, vm) for x in lst)


LIST_METHODS = {
    "len": (lambda vm, l, a: len(l), 0, 0),
    "push": (lambda vm, l, a: l.append(a[0]), 1, 1),
    "append": (lambda vm, l, a: l.append(a[0]), 1, 1),
    "pop": (l_pop, 0, 1),
    "insert": (l_insert, 2, 2),
    "remove_at": (l_remove_at, 1, 1),
    "remove": (l_remove, 1, 1),
    "index_of": (l_index_of, 1, 1),
    "contains": (lambda vm, l, a: l_index_of(vm, l, a) != -1, 1, 1),
    "slice": (lambda vm, l, a: l[slice(*_slice_bounds(len(l), a, "slice"))], 0, 2),
    "reverse": (l_reverse, 0, 0),
    "sort": (l_sort, 0, 1),
    "extend": (l_extend, 1, 1),
    "copy": (lambda vm, l, a: list(l), 0, 0),
    "clear": (lambda vm, l, a: l.clear(), 0, 0),
    "join": (l_join, 0, 1),
    "map": (lambda vm, l, a: b_map(vm, [a[0], l]), 1, 1),
    "filter": (lambda vm, l, a: b_filter(vm, [a[0], l]), 1, 1),
    "reduce": (lambda vm, l, a: b_reduce(vm, [a[0], l] + list(a[1:])), 1, 2),
    "each": (lambda vm, l, a: [vm.call_value(a[0], [x]) for x in list(l)] and None, 1, 1),
}


def d_get(vm, d, args):
    return d.get(check_hashable(args[0]), args[1] if len(args) > 1 else None)


def d_remove(vm, d, args):
    key = check_hashable(args[0])
    if key not in d:
        fault("KeyError", "key %s not found" % to_str(key, quote=True))
    return d.pop(key)


DICT_METHODS = {
    "len": (lambda vm, d, a: len(d), 0, 0),
    "keys": (lambda vm, d, a: list(d), 0, 0),
    "values": (lambda vm, d, a: list(d.values()), 0, 0),
    "items": (lambda vm, d, a: [[k, v] for k, v in d.items()], 0, 0),
    "has": (lambda vm, d, a: check_hashable(a[0]) in d, 1, 1),
    "get": (d_get, 1, 2),
    "set": (lambda vm, d, a: d.__setitem__(check_hashable(a[0]), a[1]), 2, 2),
    "remove": (d_remove, 1, 1),
    "copy": (lambda vm, d, a: dict(d), 0, 0),
    "clear": (lambda vm, d, a: d.clear(), 0, 0),
    "update": (lambda vm, d, a: d.update(expect(a[0], (dict,), "update")), 1, 1),
}

TYPE_METHODS = {str: STRING_METHODS, list: LIST_METHODS, dict: DICT_METHODS}


def install(vm):
    for name, (fn, lo, hi) in GLOBALS.items():
        vm.builtins[name] = NativeFunction(name, fn, lo, hi)
