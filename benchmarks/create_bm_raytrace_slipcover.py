from coverage import CoverageData
import json
from pathlib import Path


def main():
    b = json.load(Path("cov_data/slipcover_raytrace_black_orig.json").open("r"))
    a = CoverageData("cov_data/.coverage_raytrace_black_slipcover")
    a.erase()
    a.write()

    files_before = b["files"]
    print('b["files"]: ', files_before)
    line_data = {
        str(Path().joinpath(file).resolve()): data["executed_lines"]
        for file, data in b["files"].items()
    }

    print("line_data: ", line_data)
    a.add_lines(line_data)
    a.write()


if __name__ == "__main__":
    main()
