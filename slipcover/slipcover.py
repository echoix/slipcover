from __future__ import annotations
import sys
import dis
import types
from typing import Dict, Set, List
from collections import defaultdict, Counter
import threading
from . import tracker
from . import bytecode as bc
from . import branch as br
from pathlib import Path

# FIXME provide __all__

# Counter.total() is new in 3.10
if sys.version_info[0:2] < (3,10):
    def counter_total(self: Counter) -> int:
        return sum([self[n] for n in self])
    setattr(Counter, 'total', counter_total)


class PathSimplifier:
    def __init__(self):
        self.cwd = Path.cwd()

    def simplify(self, path : str) -> str:
        f = Path(path)
        try:
            return str(f.relative_to(self.cwd))
        except ValueError:
            return path 


class FileMatcher:
    def __init__(self):
        self.cwd = Path.cwd()
        self.sources = []
        self.omit = []

        import inspect  # usually in Python lib
        # pip is usually in site-packages; importing it causes warnings

        self.pylib_paths = [Path(inspect.__file__).parent] + \
                           [Path(p) for p in sys.path if (Path(p) / "pip").exists()]

    def addSource(self, source : Path):
        if isinstance(source, str):
            source = Path(source)
        if not source.is_absolute():
            source = self.cwd / source
        self.sources.append(source)

    def addOmit(self, omit):
        if not omit.startswith('*'):
            omit = self.cwd / omit

        self.omit.append(omit)
        pass

    def matches(self, filename : Path):
        if isinstance(filename, str):
            if filename == 'built-in': return False     # can't instrument
            filename = Path(filename)

        if filename.suffix in ('.pyd', '.so'): return False  # can't instrument DLLs

        if not filename.is_absolute():
            filename = self.cwd / filename

        if self.omit:
            from fnmatch import fnmatch
            if any(fnmatch(filename, o) for o in self.omit):
                return False

        if self.sources:
            return any(s in filename.parents for s in self.sources)

        if any(p in self.pylib_paths for p in filename.parents):
            return False

        return self.cwd in filename.parents


class Slipcover:
    def __init__(self, collect_stats: bool = False, d_threshold: int = 50, branch: bool = False):
        self.collect_stats = collect_stats
        self.d_threshold = d_threshold
        self.branch = branch

        # mutex protecting this state
        self.lock = threading.RLock()

        # maps to guide CodeType replacements
        self.replace_map: Dict[types.CodeType, types.CodeType] = dict()
        self.instrumented: Dict[str, set] = defaultdict(set)

        # notes which code lines have been instrumented
        self.code_lines: Dict[str, set] = defaultdict(set)
        self.code_branches: Dict[str, set] = defaultdict(set)

        # notes which lines and branches have been seen.
        self.all_seen: Dict[str, set] = defaultdict(set)

        # notes lines/branches seen since last de-instrumentation
        self._get_newly_seen()

        self.modules = []
        self.all_trackers = []

    def _get_newly_seen(self):
        """Returns the current set of ``new'' lines, leaving a new container in place."""

        # We trust that assigning to self.newly_seen is atomic, as it is triggered
        # by a STORE_NAME or similar opcode and Python synchronizes those.  We rely on
        # C extensions' atomicity for updates within self.newly_seen.  The lock here
        # is just to protect callers of this method (so that the exchange is atomic).

        with self.lock:
            newly_seen = self.newly_seen if hasattr(self, "newly_seen") else None
            self.newly_seen: Dict[str, set] = defaultdict(set)

        return newly_seen


    def instrument(self, co: types.CodeType, parent: types.CodeType = 0) -> types.CodeType:
        """Instruments a code object for coverage detection.

        If invoked on a function, instruments its code.
        """

        if isinstance(co, types.FunctionType):
            co.__code__ = self.instrument(co.__code__)
            return co.__code__

        assert isinstance(co, types.CodeType)
        # print(f"instrumenting {co.co_name}")

        ed = bc.Editor(co)

        # handle functions-within-functions
        for i, c in enumerate(co.co_consts):
            if isinstance(c, types.CodeType):
                ed.set_const(i, self.instrument(c, co))

        ed.add_const(tracker.hit)   # used during de-instrumentation
        tracker_signal_index = ed.add_const(tracker.signal)

        off_list = list(dis.findlinestarts(co))
        if self.branch:
            off_list.extend(list(ed.find_const_assignments(br.BRANCH_NAME)))
            off_list.sort()

        branch_set = set()

        delta = 0
        for off_item in off_list:
            if len(off_item) == 2: # from findlinestarts
                offset, lineno = off_item
                if lineno == 0: continue    # Python 3.11.0b4 generates a 0th line

                # Can't insert between an EXTENDED_ARG and the final opcode
                if (offset >= 2 and co.co_code[offset-2] == bc.op_EXTENDED_ARG):
                    while (offset < len(co.co_code) and co.co_code[offset-2] == bc.op_EXTENDED_ARG):
                        offset += 2 # TODO will we overtake the next offset from findlinestarts?

                tr = tracker.register(self, co.co_filename, lineno, self.d_threshold)
                tr_index = ed.add_const(tr)
                if self.collect_stats:
                    self.all_trackers.append(tr)

                delta += ed.insert_function_call(offset+delta, tracker_signal_index, (tr_index,))

            else: # from find_const_assignments
                begin_off, end_off, branch_index = off_item
                branch = co.co_consts[branch_index]

                branch_set.add(branch)

                tr = tracker.register(self, co.co_filename, branch, self.d_threshold)
                ed.set_const(branch_index, tr)
                if self.collect_stats:
                    self.all_trackers.append(tr)

                delta += ed.insert_function_call(begin_off+delta, tracker_signal_index, (branch_index,),
                                                 repl_length = end_off-begin_off)

        ed.add_const('__slipcover__')  # mark instrumented
        new_code = ed.finish()

        with self.lock:
            # Python 3.11.0b4 generates a 0th line
            self.code_lines[co.co_filename].update(line[1] for line in dis.findlinestarts(co) if line[1] != 0)
            self.code_branches[co.co_filename].update(branch_set)

            if not parent:
                self.instrumented[co.co_filename].add(new_code)

        return new_code


    def deinstrument(self, co, lines: set) -> types.CodeType:
        """De-instruments a code object previously instrumented for coverage detection.

        If invoked on a function, de-instruments its code.
        """

        if isinstance(co, types.FunctionType):
            co.__code__ = self.deinstrument(co.__code__, lines)
            return co.__code__

        assert isinstance(co, types.CodeType)
        # print(f"de-instrumenting {co.co_name}")

        ed = bc.Editor(co)

        co_consts = co.co_consts
        for i, c in enumerate(co_consts):
            if isinstance(c, types.CodeType):
                nc = self.deinstrument(c, lines)
                if nc is not c:
                    ed.set_const(i, nc)

        for (offset, lineno) in dis.findlinestarts(co):
            if lineno in lines and (func := ed.get_inserted_function(offset)):
                func_index = func[0]
                if co_consts[func_index] == tracker.signal:
                    tracker.deinstrument(co_consts[func[1]])

                    if not self.collect_stats:
                        ed.disable_inserted_function(offset)
                    else:
                        # If collecting stats, rather than disabling the tracker, we switch to
                        # calling the 'tracker.hit' function on it (which we conveniently added
                        # to the consts before tracker.signal, during instrumentation), so that
                        # we have the total execution count needed for the reports.
                        ed.replace_inserted_function(offset, func_index-1)

        new_code = ed.finish()
        if new_code is co:
            return co

        with self.lock:
            # Interesting (and useful fact): dict sees code edited this way as being the same
            self.replace_map[co] = new_code

            if co in self.instrumented[co.co_filename]:
                self.instrumented[co.co_filename].remove(co)
                self.instrumented[co.co_filename].add(new_code)

        return new_code


    def get_coverage(self):
        """Returns coverage information collected."""

        with self.lock:
            # FIXME calling _get_newly_seen will prevent de-instrumentation if still running!
            newly_seen = self._get_newly_seen()

            for file, lines in newly_seen.items():
                self.all_seen[file].update(lines)

            simp = PathSimplifier()

            if self.collect_stats:
                d_misses = defaultdict(Counter)
                u_misses = defaultdict(Counter)
                totals = defaultdict(Counter)
                for t in self.all_trackers:
                    filename, lineno, d_miss_count, u_miss_count, total_count = tracker.get_stats(t)
                    if d_miss_count: d_misses[filename].update({lineno: d_miss_count})
                    if u_miss_count: u_misses[filename].update({lineno: u_miss_count})
                    totals[filename].update({lineno: total_count})

            files = dict()
            for f, f_code_lines in self.code_lines.items():
                if f in self.all_seen:
                    branches_seen = {x for x in self.all_seen[f] if isinstance(x, tuple)}
                    lines_seen = self.all_seen[f] - branches_seen
                else:
                    lines_seen = branches_seen = set()

                f_files = {
                    'executed_lines': sorted(lines_seen),
                    'missing_lines': sorted(f_code_lines - lines_seen),
                }

                if self.branch:
                    f_files['executed_branches'] = sorted(branches_seen)
                    f_files['missing_branches'] = sorted(self.code_branches[f] - branches_seen)

                if self.collect_stats:
                    # Once a line reports in, it's available for deinstrumentation.
                    # Each time it reports in after that, we consider it a miss (like a cache miss).
                    # We differentiate between (de-instrument) "D misses", where a line
                    # reports in after it _could_ have been de-instrumented and (use) "U misses"
                    # and where a line reports in after it _has_ been de-instrumented, but
                    # didn't use the code object where it's deinstrumented.
                    f_files['stats'] = {
                        'd_misses_pct': round(d_misses[f].total()/totals[f].total()*100, 1),
                        'u_misses_pct': round(u_misses[f].total()/totals[f].total()*100, 1),
                        'top_d_misses': [f"{it[0]}:{it[1]}" for it in d_misses[f].most_common(5)],
                        'top_u_misses': [f"{it[0]}:{it[1]}" for it in u_misses[f].most_common(5)],
                        'top_lines': [f"{it[0]}:{it[1]}" for it in totals[f].most_common(5)],
                    }

                files[simp.simplify(f)] = f_files

            return {'files': files}


    @staticmethod
    def format_missing(missing_lines: List[int], executed_lines: List[int],
                       missing_branches: List[tuple]) -> List[str]:
        missing_set = set(missing_lines)
        missing_branches = [(a,b) for a,b in missing_branches if a not in missing_set and b not in missing_set]

        def format_branch(br):
            return f"{br[0]}->exit" if br[1] == 0 else f"{br[0]}->{br[1]}"

        """Formats ranges of missing lines, including non-code (e.g., comments) ones that fall
           between missed ones"""
        def find_ranges():
            executed = set(executed_lines)
            it = iter(missing_lines)    # assumed sorted
            a = next(it, None)
            while a is not None:
                while missing_branches and missing_branches[0][0] < a:
                    yield format_branch(missing_branches.pop(0))

                b = a
                n = next(it, None)
                while n is not None:
                    if any(l in executed for l in range(b+1, n+1)):
                        break

                    b = n
                    n = next(it, None)

                yield str(a) if a == b else f"{a}-{b}"

                a = n

            while missing_branches:
                yield format_branch(missing_branches.pop(0))

        return ", ".join(find_ranges())


    def print_coverage(self, outfile=sys.stdout) -> None:
        cov = self.get_coverage()

        from tabulate import tabulate

        def table(files):
            for f, f_info in sorted(files.items()):
                exec_l = len(f_info['executed_lines'])
                miss_l = len(f_info['missing_lines'])

                if self.branch:
                    exec_b = len(f_info['executed_branches'])
                    miss_b = len(f_info['missing_branches'])

                    pct = 100*(exec_l+exec_b)/(exec_l+miss_l+exec_b+miss_b)
                else:
                    pct = 100*exec_l/(exec_l+miss_l)

                yield [f, exec_l+miss_l, miss_l,
                       *([exec_b+miss_b, miss_b] if self.branch else []),
                       round(pct),
                       Slipcover.format_missing(f_info['missing_lines'], f_info['executed_lines'],
                                                f_info['missing_branches'] if 'missing_branches' in f_info else [])]

        print("", file=outfile)
        print(tabulate(table(cov['files']),
              headers=["File", "#lines", "#l.miss",
                       *(["#br.", "#br.miss"] if self.branch else []),
                       "Cover%", "Missing"]), file=outfile)

        def stats_table(files):
            for f, f_info in sorted(files.items()):
                stats = f_info['stats']

                yield (f, stats['d_misses_pct'], stats['u_misses_pct'],
                       " ".join(stats['top_d_misses'][:4]),
                       " ".join(stats['top_u_misses'][:4]),
                       " ".join(stats['top_lines'][:4])
                )

        if self.collect_stats:
            print("\n", file=outfile)
            print(tabulate(stats_table(cov['files']),
                           headers=["File", "D miss%", "U miss%", "Top D", "Top U", "Top lines"]),
                  file=outfile)


    @staticmethod
    def find_functions(items, visited : set):
        import inspect
        def is_patchable_function(func):
            # PyPy has no "builtin functions" like CPython. instead, it uses
            # regular functions, with a special type of code object.
            # the second condition is always True on CPython
            return inspect.isfunction(func) and type(func.__code__) is types.CodeType

        def find_funcs(root):
            if is_patchable_function(root):
                if root not in visited:
                    visited.add(root)
                    yield root

            # Prefer isinstance(x,type) over isclass(x) because many many
            # things, such as str(), are classes
            elif isinstance(root, type):
                if root not in visited:
                    visited.add(root)

                    # Don't use inspect.getmembers(root) since that invokes getattr(),
                    # which causes any descriptors to be invoked, which results in either
                    # additional (unintended) coverage and/or errors because __get__ is
                    # invoked in an unexpected way.
                    obj_names = dir(root)
                    for obj_key in obj_names:
                        mro = (root,) + root.__mro__
                        for base in mro:
                            if (base == root or base not in visited) and obj_key in base.__dict__:
                                yield from find_funcs(base.__dict__[obj_key])
                                break

            elif (isinstance(root, classmethod) or isinstance(root, staticmethod)) and \
                 is_patchable_function(root.__func__):
                if root.__func__ not in visited:
                    visited.add(root.__func__)
                    yield root.__func__

        # FIXME this may yield "dictionary changed size during iteration"
        return [f for it in items for f in find_funcs(it)]


    def register_module(self, m):
        self.modules.append(m)


    def deinstrument_seen(self) -> None:
        with self.lock:
            newly_seen = self._get_newly_seen()

            for file, new_set in newly_seen.items():
                if self.collect_stats: new_set = set(new_set)    # Counter -> set

                for co in self.instrumented[file]:
                    self.deinstrument(co, new_set)

                self.all_seen[file].update(new_set)

            # Replace references to code
            if self.replace_map:
                visited = set()

                # XXX the set of function objects could be pre-computed at register_module;
                # also, the same could be done for functions objects in globals()
                for m in self.modules:
                    for f in Slipcover.find_functions(m.__dict__.values(), visited):
                        if f.__code__ in self.replace_map:
                            f.__code__ = self.replace_map[f.__code__]

                globals_seen = []
                for frame in sys._current_frames().values():
                    while frame:
                        if not frame.f_globals in globals_seen:
                            globals_seen.append(frame.f_globals)
                            for f in Slipcover.find_functions(frame.f_globals.values(), visited):
                                if f.__code__ in self.replace_map:
                                    f.__code__ = self.replace_map[f.__code__]

                        for f in Slipcover.find_functions(frame.f_locals.values(), visited):
                            if f.__code__ in self.replace_map:
                                f.__code__ = self.replace_map[f.__code__]

                        frame = frame.f_back

                # all references should have been replaced now... right?
                self.replace_map.clear()
