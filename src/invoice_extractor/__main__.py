"""Allows ``python -m invoice_extractor``."""

import sys

from .cli import main

sys.exit(main())
