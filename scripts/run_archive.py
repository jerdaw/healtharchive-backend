#!/usr/bin/env python3
# run_archive.py
import sys

from archive_tool.main import main

# Ensure the package directory is discoverable if not installed
# For simple cases where run_archive.py is next to archive_tool/, this might not be needed
# sys.path.insert(0, os.path.dirname(__file__)) # Or adjust as necessary


if __name__ == "__main__":
    main()
