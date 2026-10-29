"""Grabbit's version - in a file of its own, so that raising it is no change to the phone app.

The phone app (the APK) is rebuilt only when something it is made from changes; a release that
changes only the Windows app carries the last APK as it was, under its own older version, and
the two numbers meet again in the next release that changes the phone app (build/release.py).
"""

APP_VERSION = '1.5.33'
