"""Shared pytest configuration for the Spoolman unit tests.

Importing ``spoolman.externaldb`` (and other modules) touches the data directory
at import time to set up the on-disk HTTP cache. Point it at a throwaway temp
directory here so unit tests never write into the real user data directory.
This runs before the test modules in this directory are imported.
"""

import os
import tempfile

os.environ.setdefault("SPOOLMAN_DIR_DATA", tempfile.mkdtemp(prefix="spoolman-unittest-"))
