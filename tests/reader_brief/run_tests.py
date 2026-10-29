"""无 pytest 环境下的最小测试运行器。"""
import sys
import traceback

sys.path.insert(0, ".")
from tests.reader_brief import test_reader_brief as t  # noqa: E402


def main():
    fns = [(n, f) for n, f in vars(t).items() if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except Exception:
            print(f"FAIL {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(fns)} total")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
