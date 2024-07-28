from coverage import CoverageData
import json
from pathlib import Path


def main():
    b = json.load(Path("cov_data/slipcover_raytrace_black_orig.json").open("r"))
    # a = CoverageData("cov_data/.coverage_raytrace_edit")
    # a = CoverageData("/workspace/slipcover/cov_data/.coverage_raytrace_black_slipcover")
    a = CoverageData("cov_data/.coverage_raytrace_black_slipcover")
    a.erase()
    a.write()
    # a.read()
    # clines = a.lines("/workspace/slipcover/benchmarks/bm_raytrace.py")
    # print("lines: ", clines)
    # measured_files = a.measured_files()
    # print("measured_files: ", measured_files)
    # # print("dumps: ", a.dumps())
    # print("measured_contexts: ", a.measured_contexts())

    files_before = b["files"]
    print('b["files"]: ', b["files"])
    line_data = {
        str(Path().joinpath(file).resolve()): data["executed_lines"]
        for file, data in b["files"].items()
    }

    # for file, data in b["files"].values():
    #     print("file: ", file)
    #     print("data: ", data)
    # for file, data in b["files"].items():
    #     print("file: ", file)
    #     print("data: ", data)
    # for file  in b["files"]:
    #     print("file: ", file)
    #     # print("data: ", data)

    print("line_data: ", line_data)
    a.add_lines(line_data)
    a.write()

if __name__ == "__main__":
    main()
