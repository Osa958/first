"""Command line entry point:  python -m tern [options] [file.tern]

  python -m tern                 start the REPL
  python -m tern prog.tern       run a program
  python -m tern --dis prog.tern print the bytecode instead of running
  python -m tern --no-opt ...    disable the peephole optimizer
  python -m tern -c "code"       run a code string
"""
import argparse
import os
import sys

from .disasm import disassemble
from .errors import TernError
from .vm import VM


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tern", description="The Tern programming language")
    ap.add_argument("file", nargs="?", help="program to run (omit for the REPL)")
    ap.add_argument("--dis", action="store_true", help="disassemble instead of running")
    ap.add_argument("--no-opt", action="store_true", help="disable the peephole optimizer")
    ap.add_argument("-c", dest="code", help="program passed in as a string")
    args = ap.parse_args(argv)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

    vm = VM(optimize=not args.no_opt)
    if args.code is None and args.file is None:
        from .repl import Repl
        Repl(vm).run()
        return 0
    try:
        if args.code is not None:
            source, filename = args.code, "<cmdline>"
        else:
            filename = os.path.abspath(args.file)
            with open(filename, encoding="utf-8") as f:
                source = f.read()
        if args.dis:
            print(disassemble(vm.compile(source, args.file or filename)))
            return 0
        if args.code is not None:
            vm.run_source(source, filename)
        else:
            vm.run_file(filename)
    except TernError as e:
        sys.stdout.flush()
        print(e.format(), file=sys.stderr)
        return 1
    except OSError as e:
        print("tern: cannot open %s: %s" % (args.file, e.strerror), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
