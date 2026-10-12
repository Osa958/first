"""Interactive read-eval-print loop with multi-line input.

Input is accumulated until it parses.  If the parser fails *at end of
input* (an unclosed block, bracket or a dangling operator) the REPL asks for
another line with a continuation prompt; an empty line forces evaluation
(reporting the error).  Globals persist between inputs.
"""
import sys

from .errors import TernError, TernSyntaxError
from .objects import to_str
from .parser import parse
from .vm import VM

BANNER = "Tern 1.0 -- type expressions or statements; ':quit' to exit, ':help' for help."
HELP = """Commands:
  :quit / :q        exit the REPL
  :help             show this help
  :dis <code>       show the bytecode for a snippet
  :reset            forget all variables
Multi-line input continues automatically while a block or bracket is open."""


def needs_more(source):
    try:
        parse(source, "<repl>")
    except TernSyntaxError as e:
        return e.at_eof
    return False


class Repl:
    def __init__(self, vm=None, inp=None, out=None):
        self.out = out or sys.stdout
        self.vm = vm or VM(out=self.out)
        self.inp = inp
        self.globals = {}

    def read_line(self, prompt):
        if self.inp is None:
            return input(prompt)
        self.out.write(prompt)
        line = self.inp.readline()
        if not line:
            raise EOFError
        return line.rstrip("\n")

    def eval(self, source):
        """Evaluate one complete input; returns the text to print (or None)."""
        try:
            result = self.vm.run_source(source, "<repl>", self.globals, repl=True)
        except TernError as e:
            # reset VM state in case an error escaped mid-execution
            self.vm.stack.clear()
            self.vm.frames.clear()
            self.vm.open_upvalues.clear()
            return e.format()
        if result is not None:
            return to_str(result, self.vm, quote=True)
        return None

    def command(self, line):
        cmd, _, rest = line.partition(" ")
        if cmd in (":quit", ":q", ":exit"):
            raise EOFError
        if cmd == ":help":
            return HELP
        if cmd == ":reset":
            self.globals = {}
            return "(state cleared)"
        if cmd == ":dis":
            from .disasm import disassemble
            try:
                return disassemble(self.vm.compile(rest, "<repl>", repl=True))
            except TernError as e:
                return e.format()
        return "unknown command %s (try :help)" % cmd

    def run(self):
        self.out.write(BANNER + "\n")
        while True:
            try:
                line = self.read_line("tern> ")
            except (EOFError, KeyboardInterrupt):
                self.out.write("\n")
                return
            if not line.strip():
                continue
            if line.startswith(":"):
                try:
                    text = self.command(line.strip())
                except EOFError:
                    return
                self.out.write(text + "\n")
                continue
            buf = [line]
            while needs_more("\n".join(buf)):
                try:
                    more = self.read_line("....> ")
                except KeyboardInterrupt:
                    self.out.write("\n(input cancelled)\n")
                    buf = None
                    break
                except EOFError:
                    break
                if not more.strip():
                    break
                buf.append(more)
            if buf is None:
                continue
            text = self.eval("\n".join(buf))
            if text is not None:
                self.out.write(text + "\n")


def main():
    Repl().run()
