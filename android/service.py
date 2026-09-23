"""Grabbit's downloader, as python-for-android starts it: in a process of its
own, beside the window's, so that the downloads outlive the window
(grabbit_mobile/host.py; DownloadService.java)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shared'))

from grabbit_mobile.host import main    # noqa: E402

main()
