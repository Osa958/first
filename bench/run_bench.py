"""Benchmark: Tern VM (optimizer on/off) vs CPython on fib(25) and a loop.

Usage: python3 bench/run_bench.py [repeats]
Reports the best of N runs (wall clock, excluding interpreter start-up and
compilation: only the execution of the compiled program is timed).
"""
import io
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.setrecursionlimit(10000)

from tern.vm import VM  # noqa: E402
from tern.objects import Closure  # noqa: E402


def time_tern(path, optimize, repeats):
    with open(path) as f:
        source = f.read()
    best = float("inf")
    output = None
    for _ in range(repeats):
        out = io.StringIO()
        vm = VM(out=out, optimize=optimize)
        proto = vm.compile(source, path)
        t0 = time.perf_counter()
        vm.call_value(Closure(proto, [], {}), [])
        best = min(best, time.perf_counter() - t0)
        output = out.getvalue().strip()
    return best, output


def time_python(path, repeats):
    with open(path) as f:
        code = compile(f.read(), path, "exec")
    best = float("inf")
    for _ in range(repeats):
        g = {"print": lambda *a: None}
        t0 = time.perf_counter()
        exec(code, g)
        best = min(best, time.perf_counter() - t0)
    return best


def main():
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print("Python %s, best of %d runs" % (sys.version.split()[0], repeats))
    fib_tern = os.path.join(HERE, "fib.tern")
    t_py = time_python(os.path.join(HERE, "fib.py"), repeats)
    t_noopt, out1 = time_tern(fib_tern, False, repeats)
    t_opt, out2 = time_tern(fib_tern, True, repeats)
    assert out1 == out2 == "75025", (out1, out2)
    print("fib(25):")
    print("  CPython                    %8.3f s" % t_py)
    print("  Tern VM, optimizer off     %8.3f s   (%.1fx CPython)" % (t_noopt, t_noopt / t_py))
    print("  Tern VM, optimizer on      %8.3f s   (%.1fx CPython)" % (t_opt, t_opt / t_py))
    print("  speedup from optimizer     %8.2fx" % (t_noopt / t_opt))
    loop = os.path.join(HERE, "loop.tern")
    l_noopt, o1 = time_tern(loop, False, repeats)
    l_opt, o2 = time_tern(loop, True, repeats)
    assert o1 == o2
    print("loop (300k iterations):")
    print("  Tern VM, optimizer off     %8.3f s" % l_noopt)
    print("  Tern VM, optimizer on      %8.3f s" % l_opt)
    print("  speedup from optimizer     %8.2fx" % (l_noopt / l_opt))


if __name__ == "__main__":
    main()
