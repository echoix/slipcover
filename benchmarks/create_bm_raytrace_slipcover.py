from coverage import CoverageData
import json
from pathlib import Path


def main():
    b = json.load(Path("cov_data/slipcover_raytrace_black_branch_orig.json").open("r"))
    a = CoverageData("cov_data/.coverage_raytrace_black_slipcover_branch")
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

    arc_data = {
        str(Path().joinpath(file).resolve()): (
            (l1, l2) for l1, l2 in data["executed_branches"]
        )
        for file, data in b["files"].items()
    }
    arc_data3 = {
        str(Path().joinpath(file).resolve()): [
            (item[0], item[1]) for item in data["executed_branches"]
        ]
        for file, data in b["files"].items()
    }

    arc_data2 = {}
    for file, data in b["files"].items():
        file2 = str(Path().joinpath(file).resolve())
        for item in data["executed_branches"]:
            print("item: ", item)
            arc = [(l1, l2) for l1, l2 in item]
            arc_data2[file2].append(arc)

        arc = [(item[0], item[1]) for item in data["executed_branches"]]
        arc_data2[file2] = arc
        # arc_data2[file2] = data

    print("arc_data: ", arc_data)
    print("arc_data: ", arc_data2)
    print("arc_data: ", arc_data3)
    a.add_lines(arc_data3)
    a.write()


if __name__ == "__main__":
    main()
