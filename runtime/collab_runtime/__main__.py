"""Starts the warm-instance service: `python -m collab_runtime`. Cloud Run runs this image as a service."""

import sys

from collab_runtime.service import serve

if __name__ == "__main__":
    sys.exit(serve())
