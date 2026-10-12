"""Language feature tests. Every `check` runs the program twice — with and
without the peephole optimizer — and requires identical, expected output."""
import textwrap
import unittest

from helpers import run, run_error


class TernTestCase(unittest.TestCase):
    def check(self, source, expected):
        expected = textwrap.dedent(expected).lstrip("\n")
        for opt in (True, False):
            with self.subTest(optimize=opt):
                self.assertEqual(run(source, optimize=opt), expected)

    def check_error(self, source, kind, fragment=""):
        for opt in (True, False):
            err, _ = run_error(source, optimize=opt)
            text = err.format()
            self.assertIn(kind, text)
            self.assertIn(fragment, text)
        return err


class TestLiteralsAndArithmetic(TernTestCase):
    def test_integers(self):
        self.check("print(1 + 2 * 3 - 4)", "3\n")

    def test_big_integers(self):
        self.check("print(2 ** 100)", "1267650600228229401496703205376\n")

    def test_floats(self):
        self.check("print(1.5 + 2.25, 10 / 4, 3.0)", "3.75 2.5 3.0\n")

    def test_int_float_mixing(self):
        self.check("print(1 + 0.5, 2 * 1.5, 7 // 2, 7 % 3, -7 // 2)", "1.5 3.0 3 1 -4\n")

    def test_division_always_float(self):
        self.check("print(6 / 3, type(6 / 3))", "2.0 float\n")

    def test_power_right_assoc(self):
        self.check("print(2 ** 3 ** 2, -2 ** 2)", "512 -4\n")

    def test_precedence_and_parens(self):
        self.check("print((1 + 2) * 3, 1 + 2 * 3, 10 - 2 - 3)", "9 7 5\n")

    def test_number_underscores_and_exponent(self):
        self.check("print(1_000_000, 1.5e3, 2E-2)", "1000000 1500.0 0.02\n")

    def test_strings_and_escapes(self):
        self.check(r'''print("a\tb", 'single', "q\"uote", "A")''', 'a\tb single q"uote A\n')

    def test_string_concat_and_repeat(self):
        self.check('print("ab" + "cd", "ab" * 3)', "abcd ababab\n")

    def test_booleans_and_null(self):
        self.check("print(true, false, null, not true, !false)", "true false null false true\n")

    def test_comparisons(self):
        self.check('print(1 < 2, 2 <= 2, 3 > 4, 4 >= 5, "a" < "b", 1 == 1.0, 1 != 2)',
                   "true true false false true true true\n")

    def test_bool_is_not_int(self):
        self.check("print(1 == true, 0 == false, true == true)", "false false true\n")

    def test_logical_short_circuit(self):
        self.check("""
            fn boom() { print("boom"); return true }
            print(false and boom())
            print(true or boom())
            print(null or "default")
            print(1 and 2)
        """, "false\ntrue\ndefault\n2\n")

    def test_truthiness(self):
        self.check("""
            for v in [0, 1, "", "x", [], [0], {}, null, 0.0] {
                if v { print("T") } else { print("F") }
            }
        """, "F\nT\nF\nT\nF\nT\nF\nF\nF\n")

    def test_in_operator(self):
        self.check('print(2 in [1, 2], "b" in "abc", "k" in {k: 1}, 5 not in [1])',
                   "true true true true\n")

    def test_constant_folding_result_same(self):
        self.check('print(2 * 3 + 4, "a" + "b", -(3), 10 / 4)', "10 ab -3 2.5\n")

    def test_comments(self):
        self.check("""
            # a comment
            print(1) # trailing
        """, "1\n")

    def test_semicolons_optional(self):
        self.check("let a = 1; let b = 2; print(a + b)", "3\n")

    def test_multiline_expressions(self):
        self.check("""
            let total = 1 +
                2 +
                3
            print(total, max(
                4,
                9,
            ))
        """, "6 9\n")


class TestVariablesAndScope(TernTestCase):
    def test_globals(self):
        self.check("let a = 1\na = a + 1\nprint(a)", "2\n")

    def test_let_without_value_is_null(self):
        self.check("let a\nprint(a)", "null\n")

    def test_block_scope(self):
        self.check("""
            let x = "global"
            {
                let x = "inner"
                print(x)
            }
            print(x)
        """, "inner\nglobal\n")

    def test_shadowing_in_nested_functions(self):
        self.check("""
            let x = 1
            fn outer() {
                let x = 2
                fn inner() {
                    let x = 3
                    return x
                }
                return [x, inner()]
            }
            print(outer(), x)
        """, "[2, 3] 1\n")

    def test_shadow_parameter_in_block(self):
        self.check("""
            fn f(a) {
                if true { let a = a * 10
                          print(a) }
                return a
            }
            print(f(4))
        """, "40\n4\n")

    def test_compound_assignment(self):
        self.check("""
            let a = 10
            a += 5
            a -= 3
            a *= 2
            a /= 4
            print(a)
            let s = "x"
            s += "y"
            print(s)
            let n = 7
            n //= 2
            n **= 2
            n %= 5
            print(n)
        """, "6.0\nxy\n4\n")

    def test_assignment_is_expression(self):
        self.check("let a\nlet b\na = b = 5\nprint(a, b)", "5 5\n")

    def test_redeclare_in_same_scope_is_error(self):
        self.check_error("fn f() { let a = 1\n let a = 2 }", "CompileError", "already declared")

    def test_global_redeclare_allowed(self):
        self.check("let a = 1\nlet a = 2\nprint(a)", "2\n")

    def test_undefined_variable(self):
        self.check_error("print(nope)", "NameError", "undefined variable 'nope'")

    def test_assign_undeclared(self):
        self.check_error("nope = 3", "NameError", "undeclared variable 'nope'")

    def test_initializer_sees_outer_binding(self):
        self.check("let x = 1\nfn f() { let x = x + 1\n return x }\nprint(f(), x)", "2 1\n")

    def test_class_name_usable_inside_own_methods(self):
        self.check("fn f() { class N { fn make() { return N() } }\n return N().make() }\nprint(f())",
                   "<N instance>\n")

    def test_mutual_recursion_globals(self):
        self.check("""
            fn is_even(n) { if n == 0 { return true } return is_odd(n - 1) }
            fn is_odd(n) { if n == 0 { return false } return is_even(n - 1) }
            print(is_even(10), is_odd(7))
        """, "true true\n")


class TestControlFlow(TernTestCase):
    def test_if_else_chain(self):
        self.check("""
            fn grade(n) {
                if n >= 90 { return "A" }
                else if n >= 80 { return "B" }
                else { return "C" }
            }
            print(grade(95), grade(85), grade(10))
        """, "A B C\n")

    def test_else_on_next_line(self):
        self.check("""
            if false {
                print("no")
            }
            else {
                print("yes")
            }
        """, "yes\n")

    def test_while(self):
        self.check("let i = 0\nwhile i < 3 { print(i)\n i += 1 }", "0\n1\n2\n")

    def test_for_in_list(self):
        self.check("for x in [3, 1, 2] { print(x) }", "3\n1\n2\n")

    def test_for_in_string_and_dict(self):
        self.check('for c in "hi" { print(c) }\nfor k in {a: 1, b: 2} { print(k) }',
                   "h\ni\na\nb\n")

    def test_for_in_range(self):
        self.check("let s = 0\nfor i in range(1, 11) { s += i }\nprint(s, range(5), range(10, 0, -3))",
                   "55 [0, 1, 2, 3, 4] [10, 7, 4, 1]\n")

    def test_break_continue_while(self):
        self.check("""
            let i = 0
            while true {
                i += 1
                if i % 2 == 0 { continue }
                if i > 7 { break }
                print(i)
            }
        """, "1\n3\n5\n7\n")

    def test_break_continue_for(self):
        self.check("""
            for i in range(10) {
                let sq = i * i
                if i == 2 { continue }
                if sq > 20 { break }
                print(sq)
            }
        """, "0\n1\n9\n16\n")

    def test_nested_loops_break_inner_only(self):
        self.check("""
            for i in range(3) {
                for j in range(3) {
                    if j == 1 { break }
                    print(i, j)
                }
            }
        """, "0 0\n1 0\n2 0\n")

    def test_break_outside_loop(self):
        self.check_error("break", "CompileError", "'break' outside of a loop")

    def test_continue_outside_loop_in_fn(self):
        self.check_error("for x in [1] { fn f() { continue } }", "CompileError", "outside of a loop")

    def test_return_at_top_level(self):
        self.check_error("return 1", "CompileError", "'return' outside of a function")

    def test_loop_mutation_of_list(self):
        self.check("""
            let xs = [1, 2, 3]
            for x in xs { if x == 1 { xs.push(4) } print(x) }
        """, "1\n2\n3\n4\n")

    def test_for_paren_header(self):
        self.check("for (x in [1, 2]) { print(x) }", "1\n2\n")


class TestFunctionsAndClosures(TernTestCase):
    def test_basic_function(self):
        self.check("fn add(a, b) { return a + b }\nprint(add(2, 3))", "5\n")

    def test_implicit_null_return(self):
        self.check("fn f() { }\nprint(f())", "null\n")

    def test_first_class_functions(self):
        self.check("""
            fn twice(f, x) { return f(f(x)) }
            fn inc(x) { return x + 1 }
            let g = inc
            print(twice(g, 5), twice(fn(x) => x * 3, 2))
        """, "7 18\n")

    def test_lambda_block_body(self):
        self.check("let f = fn(a, b) { let c = a * b\n return c + 1 }\nprint(f(3, 4))", "13\n")

    def test_counter_closure(self):
        self.check("""
            fn make_counter() {
                let count = 0
                return fn() { count += 1
                              return count }
            }
            let a = make_counter()
            let b = make_counter()
            a(); a()
            print(a(), b())
        """, "3 1\n")

    def test_closure_outlives_scope(self):
        self.check("""
            let f
            {
                let local = "captured"
                f = fn() => local
            }
            let junk1 = 1
            let junk2 = 2
            print(f())
        """, "captured\n")

    def test_closures_share_variable(self):
        self.check("""
            fn pair() {
                let v = 0
                let get = fn() => v
                let set = fn(x) { v = x }
                return [get, set]
            }
            let p = pair()
            p[1](42)
            print(p[0]())
        """, "42\n")

    def test_closure_sees_later_assignment_before_close(self):
        self.check("""
            fn f() {
                let x = 1
                let g = fn() => x
                x = 2
                return g
            }
            print(f()())
        """, "2\n")

    def test_loop_closures_capture_per_iteration(self):
        self.check("""
            let fns = []
            for i in range(3) { fns.push(fn() => i) }
            print(map(fn(f) => f(), fns))
        """, "[0, 1, 2]\n")

    def test_while_loop_closures_capture_fresh_block_var(self):
        self.check("""
            let fns = []
            let i = 0
            while i < 3 {
                let j = i
                fns.push(fn() => j * 10)
                i += 1
            }
            print(fns[0](), fns[1](), fns[2]())
        """, "0 10 20\n")

    def test_nested_closures_three_levels(self):
        self.check("""
            fn a() {
                let x = "x"
                fn b() {
                    let y = "y"
                    fn c() { return x + y }
                    return c
                }
                return b()
            }
            print(a()())
        """, "xy\n")

    def test_deeply_nested_closure_mutation(self):
        self.check("""
            fn outer() {
                let n = 0
                fn mid() {
                    fn inner() { n += 1 }
                    inner(); inner()
                }
                mid(); mid()
                return n
            }
            print(outer())
        """, "4\n")

    def test_closure_captures_parameter(self):
        self.check("fn adder(n) { return fn(x) => x + n }\nlet add5 = adder(5)\nprint(add5(10))", "15\n")

    def test_recursive_local_function(self):
        self.check("""
            fn wrapper() {
                fn fact(n) { if n <= 1 { return 1 } return n * fact(n - 1) }
                return fact(10)
            }
            print(wrapper())
        """, "3628800\n")

    def test_arity_error(self):
        self.check_error("fn f(a) { }\nf(1, 2)", "TypeError", "f() takes 1 argument but 2 were given")

    def test_call_non_callable(self):
        self.check_error("let x = 3\nx()", "TypeError", "int is not callable")

    def test_deep_recursion(self):
        self.check("""
            fn depth(n) { if n == 0 { return 0 } return 1 + depth(n - 1) }
            print(depth(50000))
        """, "50000\n")

    def test_stack_overflow_is_catchable(self):
        self.check("""
            fn forever(n) { return forever(n + 1) }
            try { forever(0) } catch e { print(type(e)) }
            print("still alive")
        """, "StackOverflowError\nstill alive\n")

    def test_function_repr(self):
        self.check("fn foo() {}\nprint(foo, print)", "<fn foo> <native fn print>\n")

    def test_memo_closure(self):
        self.check("""
            fn memoize(f) {
                let cache = {}
                return fn(n) {
                    if not cache.has(n) { cache[n] = f(n) }
                    return cache[n]
                }
            }
            let calls = 0
            let slow_sq = fn(n) { calls += 1
                                  return n * n }
            let sq = memoize(slow_sq)
            print(sq(4), sq(4), sq(5), calls)
        """, "16 16 25 2\n")


class TestCollections(TernTestCase):
    def test_list_ops(self):
        self.check("""
            let xs = [1, 2, 3]
            xs.push(4)
            print(xs, len(xs), xs[0], xs[-1])
            xs[1] = 20
            print(xs.pop(), xs)
            print(xs.contains(20), xs.index_of(3), xs.slice(1), [1, 2] + [3])
        """, "[1, 2, 3, 4] 4 1 4\n4 [1, 20, 3]\ntrue 2 [20, 3] [1, 2, 3]\n")

    def test_list_index_errors(self):
        self.check_error("let xs = [1]\nprint(xs[5])", "IndexError", "list index 5 out of range")

    def test_list_index_type_error(self):
        self.check_error('[1][\"a\"]', "TypeError", "list index must be an int")

    def test_list_sort_reverse(self):
        self.check("""
            let xs = [3, 1, 2]
            xs.sort()
            print(xs)
            xs.reverse()
            print(xs, sorted(["b", "a"]), sorted([3, 1, 2], fn(x) => -x))
        """, "[1, 2, 3]\n[3, 2, 1] [\"a\", \"b\"] [3, 2, 1]\n")

    def test_nested_lists(self):
        self.check("let m = [[1, 2], [3, 4]]\nm[1][0] += 10\nprint(m, m[1][0])", "[[1, 2], [13, 4]] 13\n")

    def test_dicts(self):
        self.check("""
            let d = {"a": 1, b: 2}
            d["c"] = 3
            print(d, d["a"], len(d), d.keys(), d.values())
            print(d.has("b"), d.get("zz", 0), d.remove("a"), d)
            print(d.items())
        """, '{"a": 1, "b": 2, "c": 3} 1 3 ["a", "b", "c"] [1, 2, 3]\n'
             'true 0 1 {"b": 2, "c": 3}\n[["b", 2], ["c", 3]]\n')

    def test_dict_non_string_keys(self):
        self.check("let d = {1: \"one\", 2.5: \"x\", null: 0}\nprint(d[1], d[2.5], d[null])", "one x 0\n")

    def test_dict_computed_key(self):
        self.check('let k = "dyn"\nlet d = {(k): 1, [k][0] + "2": 2}\nprint(d)', '{"dyn": 1, "dyn2": 2}\n')

    def test_dict_multiline_literal(self):
        self.check("""
            let d = {
                name: "tern",
                tags: ["a", "b"],
                nested: {x: 1}
            }
            print(d["nested"].len(), d["tags"][1])
        """, "1 b\n")

    def test_bool_key_rejected(self):
        self.check_error('let d = {1: "int", true: "bool"}', "TypeError", "bool cannot be used as a dictionary key")

    def test_instances_as_keys_by_identity(self):
        self.check("class K {}\nlet a = K()\nlet b = K()\nlet d = {}\nd[a] = 1\nd[b] = 2\nprint(d[a], d[b], len(d))",
                   "1 2 2\n")

    def test_missing_key(self):
        self.check_error('let d = {}\nd["x"]', "KeyError", 'key "x" not found')

    def test_unhashable_key(self):
        self.check_error("let d = {}\nd[[1]] = 2", "TypeError", "cannot be used as a dictionary key")

    def test_list_equality_deep(self):
        self.check("print([1, [2, 3]] == [1, [2, 3]], {a: [1]} == {a: [1]}, [1] == [2])", "true true false\n")

    def test_strings_indexing(self):
        self.check('let s = "hello"\nprint(s[0], s[-1], len(s))', "h o 5\n")

    def test_string_immutable(self):
        self.check_error('let s = "abc"\ns[0] = "x"', "TypeError", "strings are immutable")

    def test_aliasing(self):
        self.check("let a = [1]\nlet b = a\nb.push(2)\nprint(a)", "[1, 2]\n")

    def test_self_containing_list_prints(self):
        self.check("let a = [1]\na.push(a)\nprint(a)", "[1, [...]]\n")


class TestStdlib(TernTestCase):
    def test_map_filter_reduce(self):
        self.check("""
            let xs = range(1, 6)
            print(map(fn(x) => x * x, xs))
            print(filter(fn(x) => x % 2 == 1, xs))
            print(reduce(fn(a, b) => a + b, xs), reduce(fn(a, b) => a + b, xs, 100))
            print(xs.map(fn(x) => x + 1), xs.filter(fn(x) => x > 3), xs.reduce(fn(a, b) => a * b))
        """, "[1, 4, 9, 16, 25]\n[1, 3, 5]\n15 115\n[2, 3, 4, 5, 6] [4, 5] 120\n")

    def test_reduce_empty(self):
        self.check_error("reduce(fn(a, b) => a, [])", "ValueError", "empty sequence")

    def test_string_methods(self):
        self.check("""
            let s = "  Hello, World  "
            print(s.strip(), s.upper().strip(), s.lower().strip())
            print("a,b,c".split(","), "a b  c".split(), "-".join(["x", "y"]))
            print("hello".replace("l", "L"), "hello".find("ll"), "hello".find("z"))
            print("hello".starts_with("he"), "hello".ends_with("lo"), "hello".contains("ell"))
            print("hello".slice(1, 3), "abc".chars(), "ab".repeat(2), "x".pad_left(3, "."))
            print("{} + {} = {}".format(1, 2, 3), "123".is_digit(), "abc".count("b"))
        """, 'Hello, World HELLO, WORLD hello, world\n'
             '["a", "b", "c"] ["a", "b", "c"] x-y\n'
             'heLLo 2 -1\n'
             'true true true\n'
             'el ["a", "b", "c"] abab ..x\n'
             '1 + 2 = 3 true 1\n')

    def test_conversions(self):
        self.check("""
            print(str(42) + "!", int("17") + 1, float("2.5"), int(3.9), str(1.0), str(null))
            print(type(1), type(1.0), type("s"), type([]), type({}), type(null), type(true), type(print))
        """, "42! 18 2.5 3 1.0 null\nint float string list dict null bool function\n")

    def test_bad_int_conversion(self):
        self.check_error('int("abc")', "ValueError", "invalid int literal")

    def test_math_helpers(self):
        self.check("print(abs(-3), min(3, 1, 2), max([4, 9, 2]), sum([1, 2, 3.5]), sqrt(16), floor(2.7), ceil(2.1), round(2.5), round(3.14159, 2))",
                   "3 1 9 6.5 4.0 2 3 2 3.14\n")

    def test_chr_ord(self):
        self.check('print(ord("A"), chr(98))', "65 b\n")

    def test_len_errors(self):
        self.check_error("len(5)", "TypeError", "len() expects")

    def test_native_arity(self):
        self.check_error("len(1, 2)", "TypeError", "len() takes exactly 1 argument but 2 were given")

    def test_assert(self):
        self.check_error('assert(1 == 2, "math is broken")', "AssertionError", "math is broken")

    def test_isinstance(self):
        self.check("""
            class A {}
            class B < A {}
            print(isinstance(B(), A), isinstance(A(), B), isinstance(3, "int"), isinstance(3, A))
        """, "true false true false\n")

    def test_string_add_number_error(self):
        self.check_error('"a" + 1', "TypeError", "use str() to convert")

    def test_sort_mixed_types_error(self):
        self.check_error('[1, "a"].sort()', "TypeError", "'<' not supported between")

    def test_repr(self):
        self.check('print(repr("a\\nb"), repr([1, "x"]))', '"a\\nb" [1, "x"]\n')


class TestClasses(TernTestCase):
    def test_fields_and_methods(self):
        self.check("""
            class Point {
                fn init(x, y) { self.x = x
                                self.y = y }
                fn norm2() { return self.x * self.x + self.y * self.y }
            }
            let p = Point(3, 4)
            print(p.x, p.y, p.norm2())
            p.x = 6
            print(p.norm2())
        """, "3 4 25\n52\n")

    def test_class_without_init(self):
        self.check("class Bag {}\nlet b = Bag()\nb.item = 1\nprint(b.item, b)", "1 <Bag instance>\n")

    def test_init_arity(self):
        self.check_error("class P { fn init(a) {} }\nP()", "TypeError", "init() takes 1 argument but 0 were given")

    def test_init_returns_self(self):
        self.check("class A { fn init() { self.v = 1\n return } }\nprint(A().v)", "1\n")

    def test_init_return_value_error(self):
        self.check_error("class A { fn init() { return 1 } }", "CompileError", "Cannot return a value from an initializer")

    def test_bound_method(self):
        self.check("""
            class C { fn init(n) { self.n = n } fn get() { return self.n } }
            let m = C(7).get
            print(m())
        """, "7\n")

    def test_method_closure_captures_self(self):
        self.check("""
            class Counter {
                fn init() { self.n = 0 }
                fn incrementer() { return fn() { self.n += 1
                                                  return self.n } }
            }
            let c = Counter()
            let inc = c.incrementer()
            inc(); inc()
            print(c.n)
        """, "2\n")

    def test_inheritance_and_super(self):
        self.check("""
            class Animal {
                fn init(name) { self.name = name }
                fn speak() { return self.name + " makes a sound" }
                fn describe() { return "I am " + self.name }
            }
            class Dog < Animal {
                fn init(name, breed) { super.init(name)
                                       self.breed = breed }
                fn speak() { return super.speak() + ": woof" }
            }
            let d = Dog("Rex", "lab")
            print(d.speak())
            print(d.describe(), d.breed)
        """, "Rex makes a sound: woof\nI am Rex lab\n")

    def test_inheritance_chain_three_levels(self):
        self.check("""
            class A { fn who() { return "A" } fn chain() { return "A" } }
            class B < A { fn chain() { return super.chain() + "B" } }
            class C < B { fn chain() { return super.chain() + "C" } }
            class D < C { fn chain() { return super.chain() + "D" } }
            let d = D()
            print(d.chain(), d.who(), isinstance(d, A))
        """, "ABCD A true\n")

    def test_super_method_uses_subclass_override(self):
        self.check("""
            class Base { fn hello() { return "hello " + self.name() } fn name() { return "base" } }
            class Sub < Base { fn name() { return "sub" } fn hello() { return super.hello() + "!" } }
            print(Sub().hello())
        """, "hello sub!\n")

    def test_super_bound_method_value(self):
        self.check("""
            class A { fn f() { return "A.f" } }
            class B < A { fn f() { let g = super.f
                                   return g() + " via B" } }
            print(B().f())
        """, "A.f via B\n")

    def test_super_outside_class(self):
        self.check_error("fn f() { super.x() }", "CompileError", "'super' outside of a class")

    def test_super_without_superclass(self):
        self.check_error("class A { fn f() { super.f() } }", "CompileError", "no superclass")

    def test_self_outside_class(self):
        self.check_error("print(self)", "CompileError", "'self' outside of a class")

    def test_inherit_from_non_class(self):
        self.check_error("let X = 3\nclass Y < X {}", "TypeError", "superclass must be a class")

    def test_missing_attribute(self):
        self.check_error("class A {}\nA().nope", "AttributeError", "A instance has no attribute 'nope'")

    def test_missing_method(self):
        self.check_error("class A {}\nA().nope()", "AttributeError", "has no method 'nope'")

    def test_field_holding_function_is_callable(self):
        self.check("class A {}\nlet a = A()\na.f = fn(x) => x * 2\nprint(a.f(21))", "42\n")

    def test_to_string_used_by_print(self):
        self.check("""
            class V { fn init(x) { self.x = x } fn to_string() { return "V(" + str(self.x) + ")" } }
            print(V(1), [V(2)], str(V(3)))
        """, "V(1) [V(2)] V(3)\n")

    def test_class_in_function_scope(self):
        self.check("""
            fn make() {
                class Local { fn hi() { return "hi" } }
                return Local()
            }
            print(make().hi())
        """, "hi\n")

    def test_local_subclass_super_capture(self):
        self.check("""
            fn make(base) {
                class Sub < base { fn hi() { return super.hi() + "!" } }
                return Sub
            }
            class Base { fn hi() { return "hi" } }
            let S = make(Base)
            print(S().hi())
        """, "hi!\n")

    def test_class_attributes(self):
        self.check("class A {}\nclass B < A {}\nprint(B.name, B.superclass, A, type(B()))", "B <class A> <class A> B\n")


class TestExceptions(TernTestCase):
    def test_throw_catch_value(self):
        self.check('try { throw "oops" } catch e { print("got", e) }', "got oops\n")

    def test_catch_with_parens(self):
        self.check('try { throw 1 } catch (err) { print(err + 1) }', "2\n")

    def test_error_class(self):
        self.check("""
            try { throw ValueError("bad value") }
            catch e { print(type(e), e.message, isinstance(e, Error), e) }
        """, "ValueError bad value true ValueError: bad value\n")

    def test_custom_error_subclass(self):
        self.check("""
            class InsufficientFunds < Error {
                fn init(needed) { super.init("need " + str(needed))
                                  self.needed = needed }
            }
            try { throw InsufficientFunds(30) }
            catch e {
                if isinstance(e, InsufficientFunds) { print("short by", e.needed, "-", e.message) }
            }
        """, "short by 30 - need 30\n")

    def test_runtime_errors_are_catchable(self):
        self.check("""
            let errors = []
            let tests = [fn() => 1 / 0, fn() => [][1], fn() => {}["k"], fn() => nope, fn() => 1 + "a", fn() => null.x]
            for t in tests {
                try { t() } catch e { errors.push(type(e)) }
            }
            print(errors)
        """, '["ZeroDivisionError", "IndexError", "KeyError", "NameError", "TypeError", "AttributeError"]\n')

    def test_exception_through_several_calls(self):
        self.check("""
            fn level3() { throw Error("deep") }
            fn level2() { level3()
                          print("not reached") }
            fn level1() { level2() }
            try { level1() } catch e { print("caught:", e.message) }
            print("after")
        """, "caught: deep\nafter\n")

    def test_finally_runs_on_normal_and_error(self):
        self.check("""
            try { print("body") } finally { print("finally 1") }
            try { try { throw "x" } finally { print("finally 2") } } catch e { print("outer caught", e) }
        """, "body\nfinally 1\nfinally 2\nouter caught x\n")

    def test_finally_runs_on_return(self):
        self.check("""
            fn f() {
                try { return "from try" } finally { print("cleanup") }
            }
            print(f())
        """, "cleanup\nfrom try\n")

    def test_finally_runs_on_break_and_continue(self):
        self.check("""
            for i in range(3) {
                try {
                    if i == 0 { continue }
                    if i == 2 { break }
                    print("body", i)
                } finally { print("finally", i) }
            }
        """, "finally 0\nbody 1\nfinally 1\nfinally 2\n")

    def test_finally_after_catch_rethrow(self):
        self.check("""
            fn f() {
                try { throw "first" }
                catch e { throw "second from catch" }
                finally { print("finally ran") }
            }
            try { f() } catch e { print("outer:", e) }
        """, "finally ran\nouter: second from catch\n")

    def test_nested_try_rethrow(self):
        self.check("""
            try {
                try { throw Error("inner") }
                catch e { print("inner handler"); throw e }
            } catch e2 { print("outer handler:", e2.message) }
        """, "inner handler\nouter handler: inner\n")

    def test_try_preserves_locals(self):
        self.check("""
            fn f() {
                let a = 1
                let b = 2
                try { let c = 3
                      throw c } catch e { return a + b + e }
            }
            print(f())
        """, "6\n")

    def test_exception_unwinds_and_closes_upvalues(self):
        self.check("""
            let saved
            fn f() {
                let secret = "kept"
                saved = fn() => secret
                throw "boom"
            }
            try { f() } catch e { }
            let filler = [1, 2, 3]
            print(saved())
        """, "kept\n")

    def test_exception_from_native_callback(self):
        self.check("""
            try {
                map(fn(x) { if x == 2 { throw "bad " + str(x) } return x }, [1, 2, 3])
            } catch e { print("caught", e) }
            print(map(fn(x) => x, [1]))
        """, "caught bad 2\n[1]\n")

    def test_catch_inside_native_callback(self):
        self.check("""
            let r = map(fn(x) { try { return 10 / x } catch e { return "inf" } }, [1, 0, 2])
            print(r)
        """, '[10.0, "inf", 5.0]\n')

    def test_return_in_finally_inside_loop(self):
        self.check("""
            fn f() {
                for i in range(5) {
                    try { if i == 3 { return i } } finally { print("f", i) }
                }
            }
            print(f())
        """, "f 0\nf 1\nf 2\nf 3\n3\n")

    def test_uncaught_error_has_traceback_and_caret(self):
        err = self.check_error("""
            fn divide(a, b) {
                return a / b
            }
            fn main() { return divide(1, 0) }
            main()
        """, "ZeroDivisionError")
        text = err.format()
        self.assertIn("Traceback", text)
        self.assertIn("at divide (<test>:3:14)", text)
        self.assertIn("at main (<test>:5:26)", text)
        self.assertIn("at <module> (<test>:6:5)", text)
        self.assertIn("        return a / b\n                 ^", text)

    def test_uncaught_non_error_value(self):
        self.check_error('throw {code: 7}', "Uncaught exception", '{"code": 7}')

    def test_error_message_field_from_runtime(self):
        self.check('try { [1, 2][9] } catch e { print(e.message) }',
                   "list index 9 out of range (length 2)\n")


if __name__ == "__main__":
    unittest.main()
