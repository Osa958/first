# Tern

Tern is a small, dynamically typed programming language implemented from scratch in
Python with no third-party dependencies. Source code goes through a hand-written lexer,
a recursive-descent parser, a single-pass bytecode compiler with a peephole optimizer,
and a stack-based virtual machine.

```tern
class Account {
    fn init(owner, balance) { self.owner = owner; self.balance = balance }
    fn withdraw(amount) {
        if amount > self.balance { throw ValueError("insufficient funds") }
        self.balance -= amount
    }
}

fn make_counter() {
    let n = 0
    return fn() { n += 1; return n }      # closure over n
}

let acct = Account("ada", 100)
try { acct.withdraw(500) } catch e { print(type(e), e.message) }
let next = make_counter()
next(); next()
print(next(), map(fn(x) => x * x, range(5)))
```

```
ValueError insufficient funds
3 [0, 1, 4, 9, 16]
```

## Contents

- [Quick start](#quick-start)
- [Language tour](#language-tour)
- [Grammar](#grammar)
- [Architecture](#architecture)
- [Bytecode instruction set](#bytecode-instruction-set)
- [Design decisions](#design-decisions)
- [Standard library](#standard-library)
- [Testing](#testing)
- [Benchmark and optimization](#benchmark-and-optimization)
- [Known limitations](#known-limitations)

## Quick start

You need Python 3.10 or newer. Nothing needs to be installed.

```sh
python3 -m tern examples/fizzbuzz.tern      # run a program
python3 -m tern                             # interactive REPL
python3 -m tern --dis examples/quicksort.tern   # print the bytecode instead of running
python3 -m tern --no-opt prog.tern          # turn off the peephole optimizer
python3 -m tern -c 'print(2 ** 64)'         # run a code string
./tern.py prog.tern                         # launcher script, works from any directory
pip install -e .  &&  tern prog.tern        # optional: installs a `tern` command

python3 -m unittest discover -s tests       # 195 tests
python3 bench/run_bench.py                  # fib(25) benchmark against CPython
```

The REPL keeps reading lines while a block, bracket, or expression is still open. It
prints the value of an expression statement and keeps variables between inputs. Its
commands are `:help`, `:dis <code>`, `:reset`, and `:quit`.

```
tern> fn add(a, b) {
....>   return a + b
....> }
tern> add(2, 3)
5
tern> 1 / 0
Traceback (most recent call last):
  at <module> (<repl>:1:3)
ZeroDivisionError: division by zero
  --> <repl>:1:3
    1 / 0
      ^
```

## Language tour

| Feature | Example |
|---|---|
| Integers (arbitrary precision), floats | `2 ** 100`, `1.5e3`, `1_000_000`, `7 // 2`, `-7 % 3` |
| Strings | `"tab\t"`, `'single'`, `"é"`, `s[0]`, `"a" + "b"`, `"ab" * 3` |
| Booleans and null | `true`, `false`, `null`, `not x`, `!x`, `a and b`, `a or b` |
| Lists | `[1, "two", [3]]`, `xs[-1]`, `xs[0] = 5`, `xs.push(4)`, `[1] + [2]` |
| Dictionaries | `{"k": 1, name: "bare keys are strings", (expr): 2}`, `d["k"]`, `d.get("x", 0)` |
| Variables | `let x = 1` (block scoped), `x += 2`; `let x = x * 2` in an inner scope shadows the outer `x` |
| Functions | `fn add(a, b) { return a + b }`, `fn(x) => x * 2`, `fn(x) { ... }` |
| Closures | inner functions capture variables by reference, even after the enclosing call returns |
| Control flow | `if c {} else if d {} else {}`, `while c {}`, `for x in iterable {}`, `break`, `continue` |
| Membership | `x in list`, `"sub" in "string"`, `key in dict`, `x not in y` |
| Classes | `class Dog < Animal { fn init(n) { super.init(n) } fn speak() { ... } }` |
| Exceptions | `throw ValueError("bad")`, `try {} catch e {} finally {}`; any value can be thrown |
| Modules | `import "lib/geometry"`, `import util.strings as s`, then `geometry.Circle(2)` |

- **Statements** end at a newline or `;`. The lexer inserts a semicolon at a newline only
  when the line could end there, so a line ending in `+`, `,`, `(` or `{` continues onto
  the next line. Inside `(...)` and `[...]`, newlines are ignored completely.
- **Truthiness** follows Python: `false`, `null`, `0`, `0.0`, `""`, `[]` and `{}` are
  falsy, and everything else is truthy.
- **Equality**: `==` compares values deeply for lists and dicts and by identity for
  instances. `1 == 1.0` is true, but `1 == true` is false.
- **`print`** separates its arguments with spaces. Inside a container, strings are
  quoted. An instance with a `to_string()` method is printed using that method.

## Grammar

EBNF notation. `TERM` is either `;` or a newline turned into a semicolon by the lexer.

```ebnf
program     = { statement } EOF ;
block       = "{" { statement } "}" ;

statement   = letDecl | fnDecl | classDecl | ifStmt | whileStmt | forStmt
            | returnStmt | breakStmt | continueStmt | throwStmt | tryStmt
            | importStmt | block | exprStmt ;

letDecl     = "let" IDENT [ "=" expression ] TERM ;
fnDecl      = "fn" IDENT params block ;
params      = "(" [ IDENT { "," IDENT } [ "," ] ] ")" ;
classDecl   = "class" IDENT [ "<" IDENT { "." IDENT } ] "{" { fnDecl } "}" ;
ifStmt      = "if" expression block [ "else" ( ifStmt | block ) ] ;
whileStmt   = "while" expression block ;
forStmt     = "for" ( IDENT "in" expression | "(" IDENT "in" expression ")" ) block ;
returnStmt  = "return" [ expression ] TERM ;
breakStmt   = "break" TERM ;
continueStmt= "continue" TERM ;
throwStmt   = "throw" expression TERM ;
tryStmt     = "try" block [ "catch" [ IDENT | "(" IDENT ")" ] block ] [ "finally" block ] ;
                       (* at least one of catch / finally *)
importStmt  = "import" ( STRING | IDENT { "." IDENT } ) [ "as" IDENT ] TERM ;
exprStmt    = expression TERM ;

expression  = assignment ;
assignment  = ( IDENT | postfix "." IDENT | postfix "[" expression "]" )
              ( "=" | "+=" | "-=" | "*=" | "/=" | "//=" | "%=" | "**=" ) assignment
            | orExpr ;
orExpr      = andExpr { "or" andExpr } ;
andExpr     = notExpr { "and" notExpr } ;
notExpr     = "not" notExpr | equality ;
equality    = comparison { ( "==" | "!=" ) comparison } ;
comparison  = additive { ( "<" | "<=" | ">" | ">=" | "in" | "not" "in" ) additive } ;
additive    = multiplicative { ( "+" | "-" ) multiplicative } ;
multiplicative = unary { ( "*" | "/" | "//" | "%" ) unary } ;
unary       = ( "-" | "!" ) unary | power ;
power       = postfix [ "**" unary ] ;                 (* right associative *)
postfix     = primary { "(" [ args ] ")" | "[" expression "]" | "." IDENT } ;
args        = expression { "," expression } [ "," ] ;
primary     = INT | FLOAT | STRING | "true" | "false" | "null" | IDENT
            | "self" | "super" "." IDENT
            | "(" expression ")"
            | "[" [ expression { "," expression } [ "," ] ] "]"
            | "{" [ entry { "," entry } [ "," ] ] "}"
            | "fn" [ IDENT ] params ( block | "=>" expression ) ;
entry       = ( IDENT | expression ) ":" expression ;   (* bare IDENT key = string key *)
```

Lexical rules:

- Comments start with `#` and run to the end of the line.
- Numbers can contain `_` separators. Floats need a digit on both sides of the `.`, or an
  exponent.
- Strings can use `"` or `'`. The escapes are `\n \t \r \0 \\ \" \' \uXXXX`.
- The keywords are `let fn return if else while for in break continue class self super
  true false null and or not try catch finally throw import as`.

## Architecture

```
source ─▶ lexer.py ─▶ tokens ─▶ parser.py ─▶ AST (nodes.py)
       ─▶ compiler.py ─▶ [Instr | Label] list ─▶ optimizer.py ─▶ assemble ─▶ FunctionProto
       ─▶ vm.py (dispatch loop) + stdlib.py (natives) + objects.py (runtime types)
```

| File | Role |
|---|---|
| `tern/lexer.py` | Hand-written scanner; automatic semicolon insertion; bracket-aware newline handling |
| `tern/parser.py` | Recursive descent with one function per precedence level; errors flag whether they occurred at end of input (the REPL uses this) |
| `tern/nodes.py` | AST node classes; every node records its line and column |
| `tern/compiler.py` | Single pass over the AST; resolves locals, upvalues and globals at compile time; lowers `try`/`finally` |
| `tern/optimizer.py` | Peephole optimizer: constant folding, superinstructions, jump threading, dead-code removal |
| `tern/opcodes.py` | The instruction set, with operand counts and kinds shared by every component |
| `tern/vm.py` | The stack VM: dispatch loop, calls, exception unwinding, upvalue management, imports |
| `tern/objects.py` | Runtime object model (closures, classes, instances, upvalues, modules) and value formatting |
| `tern/stdlib.py` | Built-in functions, methods on built-in types, and the error-class prelude (written in Tern) |
| `tern/disasm.py` | Disassembler |
| `tern/repl.py` | Interactive REPL with multi-line input |

**Compiled form.** Every function compiles to a `FunctionProto` containing:

- a flat `list[int]` of bytecode (an opcode followed by its operands),
- a constant pool,
- a `(line, col)` entry for every code slot,
- the descriptors of the upvalues it captures.

The compiler emits symbolic `Label`s rather than offsets. That lets the optimizer
rewrite instruction sequences freely, and the assembler resolves the labels to absolute
offsets at the end.

**Runtime values.** Tern values map onto native Python values where possible: int, float,
str, bool, `None`, list and dict. Closures, classes, instances, bound methods, native
functions and modules use small `__slots__` classes.

## Bytecode instruction set

Each instruction is one opcode followed by a fixed number of int operands. The `Operands`
column names them: `k` is a constant-pool index, `name` is the constant-pool index of a
name string, `s` is a local slot relative to the frame base, `u` is an upvalue index,
`L` is an absolute code offset, `n` is a count, and `cmp` is the opcode of a comparison.
Stack effects are written as `before → after`, with the top of the stack on the right.

### Core instructions (emitted by the compiler)

| Opcode | Operands | Stack effect / meaning |
|---|---|---|
| `CONST` | k | `→ consts[k]` |
| `NULL` / `TRUE` / `FALSE` | – | push a literal |
| `POP` | – | `a →` |
| `DUP` | – | `a → a a` |
| `DUP2` | – | `a b → a b a b` (used by compound index assignment) |
| `GET_LOCAL` / `SET_LOCAL` | s | read / write `stack[base+s]` (SET leaves the value on the stack) |
| `GET_UPVALUE` / `SET_UPVALUE` | u | read / write a captured variable through the closure's upvalue |
| `CLOSE_UPVALUE` | – | move the stack-top variable to the heap (for closures that captured it), then pop it |
| `GET_GLOBAL` / `SET_GLOBAL` | name | look in module globals, then builtins; SET requires an existing binding |
| `DEFINE_GLOBAL` | name | `v →`, create or overwrite a module global |
| `GET_ATTR` / `SET_ATTR` | name | field, then method (as a bound method); module member; built-in type method |
| `GET_INDEX` / `SET_INDEX` | – | `obj i → obj[i]` / `obj i v → v` |
| `GET_SUPER` | name | `self super → boundmethod` |
| `ADD SUB MUL DIV FLOORDIV MOD POW` | – | `a b → a op b` |
| `EQ NE LT LE GT GE IN` | – | `a b → bool` |
| `NEG` / `NOT` | – | `a → -a` / `a → not a` |
| `JUMP` | L | `ip = L` |
| `JUMP_IF_FALSE` / `JUMP_IF_TRUE` | L | pop the condition and jump if it is falsy / truthy |
| `JUMP_IF_FALSE_OR_POP` / `JUMP_IF_TRUE_OR_POP` | L | short-circuit `and` / `or`: keep the value and jump, or pop it and continue |
| `GET_ITER` | – | `iterable → iterator` (dicts iterate over a snapshot of their keys) |
| `FOR_ITER` | L | `it → it next`, or jump to L when the iterator is exhausted |
| `CALL` | n | `f a1..an → result`; pushes a new frame for a Tern function |
| `INVOKE` | name, n | `obj a1..an → result`: a method call that skips creating a bound method |
| `SUPER_INVOKE` | name, n | `self a1..an super → result` |
| `CLOSURE` | k | build a closure from the prototype `consts[k]` and capture its upvalues |
| `RETURN` | – | return the top of the stack; close the frame's upvalues; discard the frame |
| `CLASS` | name | push a new empty class |
| `INHERIT` | – | `super cls → super`; copy the superclass's methods into the class |
| `METHOD` | name | `cls closure → cls` |
| `BUILD_LIST` / `BUILD_DICT` | n | collect n items / n key–value pairs |
| `SETUP_TRY` | L | push the handler `(L, stack height)` onto the current frame |
| `POP_TRY` | – | pop the innermost handler |
| `THROW` | – | `v →` and unwind to the nearest handler |
| `IMPORT` | k | load the module (or take it from the cache) and push it |

### Superinstructions (only produced by the peephole optimizer)

| Opcode | Operands | Replaces |
|---|---|---|
| `GET_LOCAL2` | s1, s2 | `GET_LOCAL s1; GET_LOCAL s2` |
| `STORE_LOCAL` / `STORE_GLOBAL` | s / name | `SET_LOCAL; POP` / `SET_GLOBAL; POP` |
| `ADD_CONST` / `SUB_CONST` | k | `CONST k; ADD` / `CONST k; SUB` |
| `LOCAL_ADD_CONST` / `LOCAL_SUB_CONST` | s, k | `GET_LOCAL s; CONST k; ADD/SUB` |
| `INCR_LOCAL` | s, k | `x = x + k` (stack neutral) |
| `CMP_JUMP_IF_FALSE` | cmp, L | `<cmp>; JUMP_IF_FALSE L` |
| `LOCAL_CONST_CMP_JUMP` | s, k, cmp, L | `GET_LOCAL s; CONST k; <cmp>; JUMP_IF_FALSE L` |
| `LOCAL2_CMP_JUMP` | s1, s2, cmp, L | `GET_LOCAL s1; GET_LOCAL s2; <cmp>; JUMP_IF_FALSE L` |
| `RETURN_LOCAL` | s | `GET_LOCAL s; RETURN` |

The disassembler output for `fib` looks like this:

```
$ python3 -m tern --dis bench/fib.tern
== function fib (arity 1, 0 upvalues) [bench/fib.tern:1] ==
00000    2  LOCAL_CONST_CMP_JUMP slot 1, 0 (2), <, -> 7
00005    |  RETURN_LOCAL         slot 1
00007    3  GET_GLOBAL           1 'fib'
00009    |  LOCAL_SUB_CONST      slot 1, 2 (1)
00012    |  CALL                 1
00014    |  GET_GLOBAL           1 'fib'
00016    |  LOCAL_SUB_CONST      slot 1, 0 (2)
00019    |  CALL                 1
00021    |  ADD
00022    |  RETURN
```

Columns: code offset, source line (`|` means the same line as above), opcode, and
operands annotated with their constants and names. With `--no-opt`, the same function
is 20 instructions instead of 10.

## Design decisions

**Bytecode VM, not a tree walker.**

- Calls never recurse in Python. `CALL` pushes a `Frame` onto `vm.frames` and the dispatch
  loop switches to it. Tern recursion depth is therefore limited only by `MAX_FRAMES`
  (200,000), not by Python's recursion limit: a 50,000-deep recursion runs fine, and
  infinite recursion raises a catchable `StackOverflowError`.
- All frames share one value stack. Locals live in stack slots, and slot 0 holds the
  callee, or `self` inside a method.
- The compiler maintains a fixed invariant: between statements, the stack above the frame
  base holds exactly the declared locals. Exception handlers and `break`/`continue` rely
  on it.

**Variables and scoping.**

- Top-level declarations are module globals. They are stored in a dict and resolved by
  name at runtime, so top-level functions can call each other regardless of order.
- Every other variable is a lexically scoped local, resolved to a slot or an upvalue
  index at compile time.
- Redeclaring a variable in the same block is a compile error.
- The initializer of `let x = x + 1` is compiled before `x` is declared, so it reads the
  outer `x`. Shadowing works as in Rust.
- Assigning to an undeclared name is a `NameError`, so a typo never silently creates a
  global.

**Closures that are safe to garbage-collect: Lua/clox-style upvalues.**

- When a closure captures a local, it gets an `Upvalue` object.
- While the variable's frame is alive, the upvalue is *open*: it points at the absolute
  stack slot, so the closure and the frame see the same variable.
- When the slot is about to be discarded, the VM *closes* the upvalue: it copies the value
  into the upvalue and stops pointing at the stack. This happens in three places:
  - at the end of a block that declared a captured variable (the compiler emits
    `CLOSE_UPVALUE` instead of `POP`),
  - on `RETURN`,
  - while unwinding an exception, for everything above the handler's saved stack height.
- `vm.open_upvalues` deduplicates by slot, so all closures that capture one variable
  share one `Upvalue`, and mutations made through any of them are visible to the others.
- A loop body is a fresh scope on every iteration, so closures created in a loop capture
  per-iteration variables.
- No closure ever holds a raw reference to a dead or reused stack slot. Memory itself is
  reclaimed by Python's reference counting and cycle collector, because every Tern object
  is an ordinary Python object.
- The tests check that `stack`, `frames` and `open_upvalues` are all empty after normal
  completion and after an uncaught exception unwinds 50 frames that hold closures.

**Exceptions.**

- `SETUP_TRY` records `(handler_ip, stack_height)` on the current frame.
- A throw, whether from `THROW`, from a runtime error inside the VM, or from a native
  function, pops frames until one has a handler. It then closes upvalues, truncates the
  stack to the saved height, pushes the exception, and jumps to the handler.
- `finally` is compiled by duplicating code, as CPython does. The finally block is inlined
  on the normal exit path, on the exceptional path (followed by a re-throw), and before
  every `return`, `break` or `continue` that leaves the `try`. The compiler tracks this
  with a control-context stack of loops and trys.
- Runtime errors are instances of a real Tern class hierarchy. `Error` and its subclasses
  are defined in Tern in the prelude: `TypeError`, `NameError`, `ValueError`,
  `IndexError`, `KeyError`, `AttributeError`, `ZeroDivisionError`, `IOError`,
  `ImportError`, `SyntaxError`, `AssertionError`, `StackOverflowError` and
  `InternalError`.
- User code can subclass `Error` and use `isinstance(e, Cls)` to filter.
- A traceback is captured when an exception is first thrown, and a re-thrown exception
  keeps its original traceback.

**Native functions calling back into Tern.**

- `map`, `sort` with a key function, `to_string`, imports and similar call
  `vm.call_value`, which runs a nested dispatch loop until that one call returns.
- An exception that is not caught inside the nested loop crosses back to the outer loop
  as a Python `TernThrow` and continues unwinding there. Catching inside a `map` callback
  and throwing through a `map` callback both work.

**Error messages.** Every token, AST node and code slot carries a line and column, so
every error reports `file:line:col`, the source line, and a caret:

```
prog.tern:2:5: SyntaxError: Expected variable name after 'let' but found '='
    let = 5
        ^
```

Runtime errors also show a stack trace, innermost frame last. Long traces are trimmed to
the first and last 10 frames.

**Automatic semicolons.** A newline ends a statement only when the previous token could
end one: an identifier, a literal, `)`, `]`, `}`, `return`, `break`, and so on. This uses
Go's rule, extended so that brackets are taken into account. It means newlines can be
used freely without making the grammar ambiguous. `} else` on the following line is
accepted.

**Classes.**

- Methods are copied into a subclass when it is created ("copy-down inheritance", as in
  clox), so looking up an inherited method is a single dict lookup.
- `super` is a hidden local that the class's methods capture as an upvalue, so
  `super.m()` always resolves statically to the superclass of the class that *defines*
  the method.
- `init` always returns `self`.
- `obj.m(args)` compiles to `INVOKE`, which calls the method without creating a
  bound-method object. `obj.m` as a plain value does create one.

**Modules.**

- `import "path/name"` compiles and runs `name.tern` with its own globals dict, then binds
  the resulting module object to a variable named after the file (or the `as` alias).
- The path is looked up first relative to the importing file, then the current directory,
  then each directory in `$TERN_PATH`.
- Modules are cached by absolute path, so each one runs only once. A module is cached
  *before* it runs, which makes circular imports work: the second module sees the first
  one partially initialized. If a module fails to load, it is removed from the cache.
- Every top-level name in a module is exported.

**Division.** `/` always produces a float, `//` floors, and `%` follows the sign of the
divisor. These match Python's semantics. Integers have arbitrary precision.

## Standard library

**Global functions.**

| Category | Functions |
|---|---|
| Output and input | `print(...)`, `input(prompt?)` |
| Sequences | `len(x)`, `range(stop)`, `range(start, stop, step?)` (returns a list) |
| Higher-order | `map(fn, seq)`, `filter(fn, seq)`, `reduce(fn, seq, init?)` |
| Conversions | `str`, `repr`, `int`, `float`, `bool`, `type(x)` (returns a name such as `"int"` or `"Dog"`) |
| Type checks | `isinstance(x, Class or "typename")` |
| Math | `abs`, `min`, `max`, `sum`, `sqrt`, `floor`, `ceil`, `round(x, digits?)` |
| Sorting | `sorted(seq, keyfn?)`, `reversed(seq)` |
| Dicts | `keys(d)`, `values(d)` |
| Characters | `chr`, `ord` |
| Misc | `clock()`, `assert(cond, msg?)`, `exit(code?)` |
| Files | `read_file(path)`, `write_file(path, text)`, `append_file(path, text)`, `file_exists(path)`, `delete_file(path)`. Failures raise `IOError`. |

**Methods on built-in types.**

| Type | Methods |
|---|---|
| String | `len upper lower strip trim split(sep?) join(list) replace(a, b) find index_of count contains starts_with ends_with slice(start, end?) chars repeat(n) format(...) is_digit is_alpha is_space pad_left(w, ch?) pad_right(w, ch?)`. `format` replaces each `{}` with its argument. |
| List | `len push append pop(i?) insert(i, x) remove_at(i) remove(x) index_of contains slice reverse sort(keyfn?) extend copy clear join(sep?) map filter reduce each` |
| Dict | `len keys values items has get(k, default?) set remove copy clear update` |

## Testing

```sh
python3 -m unittest discover -s tests
```

There are 195 test methods in two files.

`tests/test_language.py` covers the language itself:

- literals and arithmetic
- scoping and shadowing
- control flow
- functions and closures: per-iteration loop captures, three-level nesting, closures
  outliving their scope, recursion 50,000 frames deep, catchable stack overflow
- collections and the standard library
- classes: inheritance chains four classes deep, `super` dispatch, locally defined
  subclasses
- exceptions: thrown through several calls, `finally` on `return`/`break`/`continue`,
  re-throws, exceptions crossing native callbacks, traceback and caret formatting

Each of these tests runs **twice**, with the optimizer on and off, and both runs must
produce the expected output. This guards the optimizer against changing behavior.

`tests/test_tooling.py` covers everything around the language:

- syntax-error positions and messages
- optimizer output, plus the slow paths of the superinstructions (floats, strings, type
  errors, and the error position each one reports)
- modules: aliases, caching, isolation, circular imports, missing modules, broken modules
- file I/O
- the REPL (scripted sessions, multi-line input, recovering from an error)
- the disassembler and the CLI (exit codes, stderr output)
- VM state invariants
- golden-output tests for every example program in `examples/`

## Example programs

| File | Shows |
|---|---|
| `examples/fizzbuzz.tern` | FizzBuzz |
| `examples/fib_memo.tern` | Recursive Fibonacci memoized by a closure that owns its cache and hit counter |
| `examples/bank.tern` | `Account` → `Savings` / `Checking` → `Premium`, with `super` calls, method overrides and a custom `InsufficientFunds < Error` |
| `examples/quicksort.tern` | A functional quicksort and an in-place Lomuto quicksort using nested closures, checked against `sorted` |
| `examples/json_pretty.tern` | A JSON-like pretty printer written in Tern: recursion, string escaping, nested structures |
| `examples/modules.tern` | Imports from `examples/lib/` and subclasses a class defined in another module |
| `examples/files.tern` | File reading and writing, and error handling for I/O |

## Benchmark and optimization

`bench/run_bench.py` times only the execution of fib(25), not start-up or compilation. It
reports the best of N runs for three cases: `bench/fib.py` under CPython, the Tern VM
with the optimizer off, and the Tern VM with the optimizer on. It also times a
300,000-iteration loop.

**The optimization: superinstructions specialized by operand, with dispatch ordered by
how hot each instruction is.**

The problem it solves:

- A Python-hosted VM spends most of its time on dispatch. Every instruction is one more
  pass through `o = code[ip]` and the `if/elif` chain.
- Measured on this machine, each failed `elif` comparison costs about 15 ns. An opcode
  30 entries down the chain therefore pays about 450 ns just to be reached.
- The first version of the optimizer had generic superinstructions (`CMP_JUMP_IF_FALSE`,
  `SUB_CONST`, `RETURN_LOCAL`, …). They sat deep in the chain and cut fib's instruction
  count by only about 20%, so fib(25) did not get faster at all: 0.426 s with the
  optimizer against 0.430 s without.

What was changed:

1. The fused instructions now carry their operands directly. `LOCAL_CONST_CMP_JUMP`
   reads a local and a constant, compares them and branches, all in one dispatch, where
   it previously took 4. `LOCAL_SUB_CONST` replaces 3 dispatches, and `INCR_LOCAL`
   handles `i = i + k`.
2. Constant folding now runs to a fixpoint *before* fusion, because fusing first would
   hide folding opportunities. Writing the test for this exposed a real bug.
3. The handlers for these fused instructions sit at the top of the dispatch chain, since
   they replace the most frequently executed sequences.

The recursive path of `fib` drops from 16 dispatched instructions to 9, and the base
case from 6 to 2.

**Results** (Python 3.13, fib(25) = 75025, best of 5 runs; repeated rounds varied by
about ±15% on this shared machine):

| Configuration | fib(25) | vs CPython |
|---|---|---|
| CPython 3.13, plain recursive function | 0.008 s | 1× |
| Tern, *before*: baseline commit `f11afa6`, optimizer on | 0.40–0.43 s | ~55× |
| Tern, final VM, optimizer off (`--no-opt`) | 0.55–0.62 s | ~75× |
| Tern, *after*: final VM, optimizer on (the default) | **0.31 s** | **~40×** |

- Compared with the baseline commit, fib(25) is about **1.35× faster** (0.42 s → 0.31 s).
- In the final VM, turning the optimizer on makes fib(25) about **1.8× faster**.
- On the loop benchmark it makes the program about **2× faster** (1.15 s → 0.56 s).

The final VM with the optimizer *off* is slower than the baseline commit. That is the
expected trade-off: moving the superinstructions to the top of the dispatch chain moves
the plain instructions further down. A Tern VM written in Python cannot get close to
CPython, which runs the same function as native-code dispatch over its own specialized
bytecode. Being about 40× slower is typical for this kind of design.

## Known limitations

- **Speed.** Programs run about 40–75× slower than equivalent CPython code. There is no
  JIT, and there are no inline caches for global or attribute lookups.
- **Memory management** is delegated to Python's GC. The VM ensures closures are correct,
  but it has no collector of its own and no memory limits.
- **Recursion through natives.** A native function that calls back into Tern (`map`,
  `filter`, `reduce`, `sort` with a key function, `to_string`) uses Python recursion. So
  Tern recursion that passes *through* such callbacks is limited by Python's recursion
  limit, which the CLI raises to 20,000. Plain Tern-to-Tern recursion is not limited
  this way.
- **Functions** have no default arguments, variadic parameters or keyword arguments
  (native functions can be variadic). There are no static methods, class fields,
  properties, operator overloading, iterator protocol for user classes, or `instanceof`
  operator; `isinstance()` covers the last.
- **Missing syntax.** There is no slice syntax (use `.slice()`), no string interpolation
  (use `"{}".format(x)` or `+`), no `switch`/`match`, no `do-while`, and no constants.
- `range()` builds a full list instead of being lazy.
- **Dictionaries** have insertion-ordered keys of type int, float, string, null or
  instance (instances by identity). Bool keys are rejected: Python would merge `true`
  with `1`, but in Tern `1 != true`. `1` and `1.0` are the same key, as in Python.
- `catch` catches everything. To filter by type, test with `isinstance` and re-throw.
- **Modules** export every top-level name, and there are no private names. A cycle of
  imports sees partially initialized modules.
- Strings are immutable Python strings, and indexing works on Unicode code points. File
  I/O is text-only and always UTF-8.
- `round()` uses banker's rounding, as Python does: `round(2.5) == 2`.
