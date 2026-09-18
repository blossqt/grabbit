"""Entry point for the packaged exe and for `python app\\main.py`.

`python -m grabbit` uses grabbit/__main__.py instead; PyInstaller cannot, because
a script it runs directly has no parent package for relative imports.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grabbit.app import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
