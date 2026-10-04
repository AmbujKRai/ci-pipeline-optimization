"""Container HEALTHCHECK probe: exit code 0 when /health answers 200, otherwise 1."""

from __future__ import annotations

import os
import sys
import urllib.request


def main() -> int:
    url = f"http://127.0.0.1:{os.getenv('PORT', '8000')}/health"
    try:
        # The URL is a fixed loopback address, never user input.
        with urllib.request.urlopen(url, timeout=3) as response:  # nosec B310
            return 0 if response.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
