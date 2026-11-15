import sys

from collab_runtime.main import main

if __name__ == "__main__":
    # `serve` starts the warm-instance HTTP service. No arguments runs one job invocation (see main.py).
    if sys.argv[1:] == ["serve"]:
        from collab_runtime.service import serve

        sys.exit(serve())
    sys.exit(main())
