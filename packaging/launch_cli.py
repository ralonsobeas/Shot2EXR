"""PyInstaller entry point for the console ``shot2exr-cli`` command."""

import sys

from shot2exr.cli import main

sys.exit(main())
