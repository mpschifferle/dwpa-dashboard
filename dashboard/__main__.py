"""Allow "python -m dashboard" to start the dashboard."""

import sys

from dashboard.app import main

if __name__ == "__main__":
    sys.exit(main())
