"""The public half of the key Grabbit's releases are signed with.

Written once by build/release.py --make-update-key. The private half stays on
the machine that publishes, at ~/.grabbit/release/update-signing.key, and every
copy of Grabbit installs only updates whose manifest it signed (updates.py).
Changing this line strands every installed copy: they would refuse all later
updates.
"""

PUBLIC_KEY = '73c12e6787d3b5271ef6e3d3cca0a3e63c9c1aae998c5e390e94ef367b0e7c20'
