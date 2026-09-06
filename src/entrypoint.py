"""Top-level packaged entry point.

Keep the script outside the ``iPhoto`` package so Nuitka adds ``src`` rather
than ``src/iPhoto`` to its top-level import search path.  The latter would make
``iPhoto/io`` shadow Python's bootstrap-critical standard-library ``io``
module before the interpreter can initialise its streams.
"""

from iPhoto.entrypoint import main


if __name__ == "__main__":
    raise SystemExit(main())
