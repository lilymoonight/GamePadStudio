"""Compatibility entry point for the shared per-module platform test runner."""
from test_modules import main


if __name__ == '__main__':
    raise SystemExit(main())
