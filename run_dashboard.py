#!/usr/bin/env python3
"""
Start the help_crack web dashboard.

    python run_dashboard.py                # http://127.0.0.1:8666/
    python run_dashboard.py --open         # ... and open the browser
    python run_dashboard.py --workdir D:\\crack --port 9000

All arguments are forwarded to dashboard/app.py, see --help for details.
"""

import sys

from dashboard.app import main

if __name__ == "__main__":
    sys.exit(main())
