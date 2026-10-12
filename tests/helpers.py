import io
import os
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tern.errors import TernError  # noqa: E402
from tern.vm import VM  # noqa: E402

sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))


def run(source, optimize=True, filename="<test>", **vm_kwargs):
    """Run Tern source and return everything it printed."""
    out = io.StringIO()
    vm = VM(out=out, optimize=optimize, **vm_kwargs)
    vm.run_source(textwrap.dedent(source), filename)
    return out.getvalue()


def run_error(source, optimize=True, filename="<test>"):
    """Run Tern source that must fail; returns (error, printed output)."""
    out = io.StringIO()
    vm = VM(out=out, optimize=optimize)
    try:
        vm.run_source(textwrap.dedent(source), filename)
    except TernError as e:
        return e, out.getvalue()
    raise AssertionError("expected a Tern error, program printed:\n" + out.getvalue())


def run_file(path, optimize=True):
    out = io.StringIO()
    vm = VM(out=out, optimize=optimize)
    vm.run_file(path)
    return out.getvalue()
