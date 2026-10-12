"""Tests for error reporting, the optimizer, modules, I/O, REPL, disassembler,
CLI and the example programs."""
import io
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

from helpers import ROOT, run, run_error, run_file

from tern import opcodes as O
from tern.compiler import compile_source
from tern.disasm import disassemble
from tern.errors import TernSyntaxError
from tern.repl import Repl, needs_more
from tern.vm import VM


def opcodes_of(proto):
    names = []
    ip = 0
    while ip < len(proto.code):
        names.append(O.NAMES[proto.code[ip]])
        ip += 1 + len(O.OPERANDS[proto.code[ip]])
    return names


class TestSyntaxErrors(unittest.TestCase):
    def syntax(self, source):
        with self.assertRaises(TernSyntaxError) as cm:
            run(source)
        return cm.exception

    def test_position_and_caret(self):
        e = self.syntax("let a = 1\nlet = 5\n")
        self.assertEqual((e.line, e.col), (2, 5))
        self.assertIn("Expected variable name after 'let' but found '='", e.format())
        self.assertIn("<test>:2:5: SyntaxError", e.format())
        self.assertTrue(e.format().endswith("    let = 5\n        ^"))

    def test_unclosed_call_at_eof(self):
        e = self.syntax("print(1, 2")
        self.assertTrue(e.at_eof)
        self.assertIn("Expected ')' after arguments but found end of input", e.message)

    def test_unterminated_string(self):
        e = self.syntax('let s = "abc\nprint(s)')
        self.assertEqual((e.line, e.col), (1, 9))
        self.assertIn("Unterminated string", e.message)

    def test_unexpected_character(self):
        e = self.syntax("let x = 3 @ 4")
        self.assertEqual(e.col, 11)
        self.assertIn("Unexpected character '@'", e.message)

    def test_dangling_operator(self):
        e = self.syntax("let x = 1 +")
        self.assertTrue(e.at_eof)

    def test_invalid_assignment_target(self):
        e = self.syntax("1 = 2")
        self.assertIn("Invalid assignment target", e.message)
        self.assertEqual(e.col, 3)

    def test_duplicate_parameter(self):
        self.assertIn("Duplicate parameter 'a'", self.syntax("fn f(a, a) {}").message)

    def test_unmatched_brace(self):
        self.assertIn("Unmatched '}'", self.syntax("let x = 1 }").message)

    def test_missing_statement_separator(self):
        e = self.syntax("let a = 1 let b = 2")
        self.assertIn("Expected newline or ';' after statement but found 'let'", e.message)

    def test_bad_escape(self):
        e = self.syntax(r'print("\q")')
        self.assertIn("Unknown escape sequence", e.message)
        self.assertEqual(e.col, 8)

    def test_class_body_must_have_methods(self):
        self.assertIn("Expected method declaration", self.syntax("class A { let x = 1 }").message)

    def test_try_needs_catch_or_finally(self):
        self.assertIn("Expected 'catch' or 'finally'", self.syntax("try { }").message)

    def test_error_on_later_line_reports_that_line(self):
        e = self.syntax("fn ok() {\n    return 1\n}\nfn bad( {\n}")
        self.assertEqual(e.line, 4)

    def test_compile_error_has_caret(self):
        e = self.syntax("fn f() {\n  break\n}")
        self.assertIn("CompileError: 'break' outside of a loop", e.format())
        self.assertIn("  break\n      ^", e.format())


class TestOptimizer(unittest.TestCase):
    def test_constant_folding(self):
        proto = compile_source("let x = 2 * 3 + 4 - -1")
        self.assertEqual(opcodes_of(proto)[:2], ["CONST", "DEFINE_GLOBAL"])
        self.assertIn(11, proto.consts)

    def test_no_fold_division_by_zero(self):
        proto = compile_source("let x = 1 / 0")
        self.assertIn("DIV", opcodes_of(proto))
        err, _ = run_error("let x = 1 / 0")
        self.assertIn("ZeroDivisionError", err.format())

    def test_fib_superinstructions(self):
        proto = compile_source("fn fib(n) { if n < 2 { return n } return fib(n - 1) + fib(n - 2) }")
        fib = proto.consts[0]
        ops = opcodes_of(fib)
        self.assertEqual(ops[0], "LOCAL_CONST_CMP_JUMP")
        self.assertIn("RETURN_LOCAL", ops)
        self.assertEqual(ops.count("LOCAL_SUB_CONST"), 2)
        unopt = opcodes_of(compile_source("fn fib(n) { if n < 2 { return n } return fib(n - 1) + fib(n - 2) }",
                                          optimize=False).consts[0])
        self.assertLess(len(ops), len(unopt))

    def test_dead_code_removed(self):
        proto = compile_source("fn f() { return 1\n print(2) }").consts[0]
        self.assertNotIn("GET_GLOBAL", opcodes_of(proto))

    def test_increment_superinstruction(self):
        proto = compile_source("fn f() { let i = 0\n while i < 10 { i = i + 1 } }").consts[0]
        self.assertIn("INCR_LOCAL", opcodes_of(proto))

    def test_fused_ops_slow_paths(self):
        # floats / strings flow through the superinstructions' non-int paths
        src = """
            fn f(x) { if x < 2.5 { return "small" } return "big" }
            fn g(x) { let y = x
                      y = y + 1
                      return [y, x - 1] }
            fn h(a, b) { if a < b { return "lt" } return "ge" }
            print(f(1), f(2.5), f(3), g(1.5), h("a", "b"), h(2.0, 1))
        """
        for opt in (True, False):
            self.assertEqual(run(src, optimize=opt), 'small big big [2.5, 0.5] lt ge\n')

    def test_fused_op_type_error_position(self):
        src = 'fn f(x) {\n    if x < 2 { return 1 }\n}\nf("s")'
        for opt in (True, False):
            err, _ = run_error(src, optimize=opt)
            self.assertIn("TypeError: '<' not supported between string and int", err.format())
            self.assertIn("at f (<test>:2:10)", err.format())

    def test_fused_sub_const_type_error(self):
        for opt in (True, False):
            err, _ = run_error('fn f(x) { return x - 1 }\nf("a")', optimize=opt)
            self.assertIn("unsupported operand types for -: string and int", err.format())


class TestModules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(textwrap.dedent(text))
        return path

    def test_import_and_alias(self):
        self.write("util/mathx.tern", "let TWO = 2\nfn double(x) { return x * TWO }\n")
        main = self.write("main.tern", 'import "util/mathx"\nimport util.mathx as m\n'
                                       'print(mathx.double(21), m.TWO, mathx == m)\n')
        self.assertEqual(run_file(main), "42 2 true\n")

    def test_module_loaded_once(self):
        self.write("counter.tern", 'print("loading counter")\nlet n = 0\n')
        self.write("a.tern", 'import "counter"\n')
        main = self.write("main.tern", 'import "counter"\nimport "a"\nprint(counter.n)\n')
        self.assertEqual(run_file(main), "loading counter\n0\n")

    def test_module_globals_are_isolated(self):
        self.write("m.tern", "let x = \"module x\"\nfn get_x() { return x }\n")
        main = self.write("main.tern", 'let x = "main x"\nimport "m"\nprint(m.get_x(), x)\n')
        self.assertEqual(run_file(main), "module x main x\n")

    def test_circular_import(self):
        self.write("a.tern", 'import "b"\nlet name = "a"\nfn hello() { return "hello from " + b.name }\n')
        self.write("b.tern", 'import "a"\nlet name = "b"\nfn peer() { return a.name }\n')
        main = self.write("main.tern", 'import "a"\nimport "b"\nprint(a.hello(), b.peer())\n')
        self.assertEqual(run_file(main), "hello from b a\n")

    def test_missing_module(self):
        main = self.write("main.tern", 'try { import "nope" } catch e { print(type(e), e.message) }\n')
        self.assertEqual(run_file(main), "ImportError cannot find module 'nope.tern'\n")

    def test_missing_attribute(self):
        self.write("m.tern", "let a = 1\n")
        main = self.write("main.tern", 'import "m"\nprint(m.b)\n')
        with self.assertRaises(Exception) as cm:
            run_file(main)
        self.assertIn("module 'm' has no attribute 'b'", str(cm.exception))

    def test_syntax_error_in_module(self):
        self.write("broken.tern", "fn (\n")
        main = self.write("main.tern", 'try { import "broken" } catch e { print(type(e)) }\n')
        self.assertEqual(run_file(main), "SyntaxError\n")

    def test_error_inside_module_traceback(self):
        self.write("bad.tern", "fn explode() { return [][0] }\n")
        main = self.write("main.tern", 'import "bad"\nbad.explode()\n')
        with self.assertRaises(Exception) as cm:
            run_file(main)
        text = str(cm.exception)
        self.assertIn("IndexError", text)
        self.assertIn("bad.tern:1:", text)


class TestFileIO(unittest.TestCase):
    def test_write_read_append(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "out.txt").replace("\\", "/")
            src = f'''
                write_file("{path}", "a\\n")
                append_file("{path}", "b\\n")
                print(read_file("{path}").split("\\n"), file_exists("{path}"))
                delete_file("{path}")
                print(file_exists("{path}"))
            '''
            self.assertEqual(run(src), '["a", "b", ""] true\nfalse\n')

    def test_read_missing_file(self):
        err, _ = run_error('read_file("/definitely/not/here.txt")')
        self.assertIn("IOError", err.format())

    def test_write_requires_string(self):
        err, _ = run_error('write_file("x.txt", 3)')
        self.assertIn("write_file() expects string content, got int", err.format())


class TestRepl(unittest.TestCase):
    def session(self, text):
        out = io.StringIO()
        Repl(inp=io.StringIO(textwrap.dedent(text)), out=out).run()
        return out.getvalue()

    def test_multiline_and_state(self):
        out = self.session("""\
            let x = 10
            x * 2
            fn add(a, b) {
              return a + b
            }
            add(x, 5)
            [1, 2,
             3]
            print("hi")
            1 / 0
            x
            :quit
        """)
        self.assertIn("tern> 20\n", out)
        self.assertIn("....> ....> tern> 15\n", out)
        self.assertIn("[1, 2, 3]\n", out)
        self.assertIn("hi\n", out)
        self.assertIn("ZeroDivisionError: division by zero", out)
        self.assertTrue(out.rstrip().endswith("tern> 10\ntern>"))

    def test_syntax_error_does_not_kill_repl(self):
        out = self.session("let = 1\nlet ok = 1\nok\n")
        self.assertIn("SyntaxError", out)
        self.assertIn("tern> 1\n", out)

    def test_string_results_are_quoted(self):
        self.assertIn('"abc"', self.session('"abc"\n'))

    def test_needs_more(self):
        self.assertTrue(needs_more("fn f() {"))
        self.assertTrue(needs_more("let x = ["))
        self.assertTrue(needs_more("if x {\n print(1)"))
        self.assertFalse(needs_more("let x = 1"))
        self.assertFalse(needs_more("let = 1"))      # real error, not incomplete

    def test_dis_command(self):
        self.assertIn("ADD", self.session(":dis a + b\n"))


class TestDisassembler(unittest.TestCase):
    def test_listing(self):
        proto = compile_source("fn outer() { let x = 1\n return fn() => x }\nprint(outer()())", "d.tern")
        text = disassemble(proto)
        self.assertIn("== script <module>", text)
        self.assertIn("== function outer (arity 0", text)
        self.assertIn("== function <lambda> (arity 0, 1 upvalue)", text)
        self.assertIn("captures: local 1", text)
        self.assertIn("GET_UPVALUE", text)
        self.assertIn("CLOSURE", text)
        self.assertIn("'print'", text)

    def test_every_opcode_has_operand_spec(self):
        self.assertEqual(set(O.NAMES), set(O.OPERANDS))


class TestExamplesAndCli(unittest.TestCase):
    def example(self, name):
        path = os.path.join(ROOT, "examples", name + ".tern")
        with open(os.path.join(ROOT, "tests", "expected", name + ".out")) as f:
            expected = f.read()
        for opt in (True, False):
            self.assertEqual(run_file(path, optimize=opt), expected)

    def test_fizzbuzz(self):
        self.example("fizzbuzz")

    def test_fib_memo(self):
        self.example("fib_memo")

    def test_bank(self):
        self.example("bank")

    def test_quicksort(self):
        self.example("quicksort")

    def test_json_pretty(self):
        self.example("json_pretty")

    def test_modules(self):
        self.example("modules")

    def cli(self, *args, cwd=None):
        env = dict(os.environ, PYTHONPATH=ROOT)
        return subprocess.run([sys.executable, "-m", "tern", *args], cwd=cwd or ROOT,
                              capture_output=True, text=True, timeout=120, env=env)

    def test_cli_runs_file(self):
        r = self.cli("examples/fizzbuzz.tern")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("1\n2\nFizz\n"))

    def test_cli_error_exit_code_and_stderr(self):
        r = self.cli("-c", "let x = [1]\nprint(x[3])")
        self.assertEqual(r.returncode, 1)
        self.assertIn("IndexError: list index 3 out of range", r.stderr)
        self.assertIn("print(x[3])\n           ^", r.stderr)

    def test_cli_disassemble(self):
        r = self.cli("--dis", "bench/fib.tern")
        self.assertEqual(r.returncode, 0)
        self.assertIn("LOCAL_CONST_CMP_JUMP", r.stdout)
        r = self.cli("--dis", "--no-opt", "bench/fib.tern")
        self.assertNotIn("LOCAL_CONST_CMP_JUMP", r.stdout)

    def test_files_example(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.cli(os.path.join(ROOT, "examples", "files.tern"), cwd=d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('read 2 lines: ["first line", "second line"]', r.stdout)
            self.assertIn("IOError", r.stdout)


class TestVMInternals(unittest.TestCase):
    def test_vm_state_clean_after_run(self):
        vm = VM(out=io.StringIO())
        vm.run_source("fn f() { let a = 1\n return fn() => a }\nlet g = f()\ntry { throw 1 } catch e {}")
        self.assertEqual(vm.stack, [])
        self.assertEqual(vm.frames, [])
        self.assertEqual(vm.open_upvalues, {})

    def test_vm_state_clean_after_uncaught(self):
        vm = VM(out=io.StringIO())
        with self.assertRaises(Exception):
            vm.run_source("fn f(n) { let x = fn() => n\n if n == 0 { throw 1 } return f(n - 1) }\nf(50)")
        self.assertEqual(vm.stack, [])
        self.assertEqual(vm.frames, [])
        self.assertEqual(vm.open_upvalues, {})

    def test_call_value_from_python(self):
        vm = VM(out=io.StringIO())
        g = {}
        vm.run_source("fn add(a, b) { return a + b }", "<t>", g)
        self.assertEqual(vm.call_value(g["add"], [2, 3]), 5)

    def test_many_closures_in_deep_recursion(self):
        src = """
            fn build(n, acc) {
                if n == 0 { return acc }
                let v = n
                acc.push(fn() => v)
                return build(n - 1, acc)
            }
            let fs = build(3000, [])
            print(len(fs), fs[0](), fs[2999]())
        """
        self.assertEqual(run(src), "3000 3000 1\n")


if __name__ == "__main__":
    unittest.main()
