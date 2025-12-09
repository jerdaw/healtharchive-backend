# archive_tool/__main__.py
import sys

# Use absolute import from the package root
from archive_tool.main import main

if __name__ == "__main__":
    # Optionally add setup here if needed, e.g., argument preprocessing
    # Ensure PATH includes necessary executables like docker, nordvpn if needed
    main()
