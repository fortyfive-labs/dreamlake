#!/usr/bin/env python3
"""Refuse to start a publish for a version PyPI already has.

PyPI files are immutable: a filename that exists can never be replaced, only
yanked. So "upload and see" has exactly two outcomes for an already-published
version — a 400 from the middle of the upload, or, with ``--skip-existing``, a
green run that published nothing. Neither is a useful answer, and the second is
worse than the first because it looks like success.

This asks before anything is built or uploaded, and says which of the three
things is true:

    404            not published; the publish may proceed
    200            already published; stop, and say so plainly
    anything else  stop. A 500 or a DNS failure is not evidence that a version
                   is absent, and proceeding on that assumption is how a
                   half-finished release gets started.

Reads the public JSON API. Nothing is installed, executed or authenticated.
"""

import argparse
import sys
import time
import urllib.error
import urllib.request

ATTEMPTS = 3
RETRY_WAIT_SECONDS = 2.0
TIMEOUT_SECONDS = 30


def published(project, version):
    """True if the version exists, False if it does not. Raises if unknown."""
    url = "https://pypi.org/pypi/%s/%s/json" % (project, version)
    last = None
    for attempt in range(ATTEMPTS):
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status == 200:
                    return True
                last = "HTTP %d" % response.status
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            last = "HTTP %d" % exc.code
            if exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = "%s: %s" % (type(exc).__name__, exc)
        if attempt + 1 < ATTEMPTS:
            time.sleep(RETRY_WAIT_SECONDS)
    raise RuntimeError("%s could not be read (%s)" % (url, last))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)

    try:
        exists = published(args.project, args.version)
    except RuntimeError as exc:
        print("REFUSED: %s" % exc, file=sys.stderr)
        print("PyPI's state is unknown, so no build or upload is started.", file=sys.stderr)
        return 2

    if exists:
        print(
            "REFUSED: %s %s is already on PyPI.\n"
            "PyPI files are immutable — this version cannot be replaced or "
            "re-uploaded. Release a new version instead."
            % (args.project, args.version),
            file=sys.stderr,
        )
        return 1

    print("ok: %s %s is not on PyPI yet" % (args.project, args.version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
