from coverage import CoverageData


def main():
    # a = CoverageData("cov_data/.coverage_raytrace_edit")
    a = CoverageData("/workspace/slipcover/cov_data/.coverage_raytrace_black_edit")
    a.read()
    clines = a.lines("/workspace/slipcover/benchmarks/bm_raytrace.py")
    print("lines: ", clines)
    measured_files = a.measured_files()
    print("measured_files: ", measured_files)
    # print("dumps: ", a.dumps())
    print("measured_contexts: ", a.measured_contexts())


def main2():
    # a = CoverageData("cov_data/.coverage_raytrace_edit")
    a = CoverageData("/workspace/slipcover/cov_data/.coverage_raytrace_black_slipcover")
    a.read()
    clines = a.lines("/workspace/slipcover/benchmarks/bm_raytrace.py")
    print("lines: ", clines)
    measured_files = a.measured_files()
    print("measured_files: ", measured_files)
    # print("dumps: ", a.dumps())
    print("measured_contexts: ", a.measured_contexts())


if __name__ == "__main__":
    # main()
    main2()
