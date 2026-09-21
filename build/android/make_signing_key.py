r"""Make the key every Grabbit APK is signed with. Once.

    python build/android/make_signing_key.py            # make it (refuses if one exists)
    python build/android/make_signing_key.py --upload   # put an existing key back into GitHub

Android installs an update only when it is signed with the same key as the app
it replaces, so this key is the app's identity. Lose it and every copy already
on a phone can never be updated again - only uninstalled and installed afresh.

It ends up in three places:

  ~/.grabbit/android/release.keystore    the only copy you hold. BACK IT UP.
  the ANDROID_KEYSTORE_B64 secret        where the Android workflow reads it
  build/android/signing-sha256.txt       the certificate's fingerprint. That
                                         part is public - every APK carries the
                                         certificate - and it pins which key a
                                         build and a release must be signed with.

It is kept in the form Gradle's debug signing expects (alias androiddebugkey,
password "android"), because what p4a builds is a debug APK: that is what lets
build/android/device_test.ps1 reach the app with run-as. The password guards
nothing; keeping the file to yourself is what does.

Making a key needs the cryptography package, and only this script needs it;
use a throwaway environment rather than the build venv, so it cannot end up
bundled into the Windows app:

    python -m venv %TEMP%\grabbit-key && %TEMP%\grabbit-key\Scripts\pip install cryptography
    %TEMP%\grabbit-key\Scripts\python build\android\make_signing_key.py
"""

import argparse
import base64
import datetime
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = 'blossqt/grabbit'
SECRET = 'ANDROID_KEYSTORE_B64'
KEYSTORE = Path.home() / '.grabbit' / 'android' / 'release.keystore'
PIN = HERE / 'signing-sha256.txt'

ALIAS, PASSWORD = b'androiddebugkey', b'android'
VALIDITY_DAYS = 10000          # about 27 years: Gradle's own debug key uses the same


def fail(message):
    print(f'error: {message}', file=sys.stderr)
    sys.exit(1)


def gh():
    found = shutil.which('gh') or next(
        (str(p) for p in (Path(r'C:\Program Files\GitHub CLI\gh.exe'),) if p.exists()), None)
    if not found:
        fail("GitHub's command-line tool is not installed (winget install GitHub.cli)")
    return found


def upload(keystore: Path):
    """Store the keystore as the workflow's secret. Read from stdin, never argv."""
    encoded = base64.b64encode(keystore.read_bytes()).decode('ascii')
    result = subprocess.run([gh(), 'secret', 'set', SECRET, '--repo', REPO],
                            input=encoded, text=True, capture_output=True)
    if result.returncode != 0:
        fail(f'could not set the {SECRET} secret: {result.stderr.strip()}')
    print(f'set the {SECRET} secret on {REPO}')


def make() -> str:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import pkcs12
        from cryptography.x509.oid import NameOID
    except ImportError:
        fail('making a key needs the cryptography package - see the top of this file')

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Grabbit'),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'blossqt')])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder()
                   .subject_name(name).issuer_name(name)
                   .public_key(key.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(days=1))
                   .not_valid_after(now + datetime.timedelta(days=VALIDITY_DAYS))
                   .sign(key, hashes.SHA256()))

    # The older PKCS#12 ciphers, which every Java keytool reads; the newer
    # ones cryptography prefers are not understood by all of them.
    encryption = (serialization.PrivateFormat.PKCS12.encryption_builder()
                  .kdf_rounds(50000)
                  .key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
                  .hmac_hash(hashes.SHA1())
                  .build(PASSWORD))
    KEYSTORE.parent.mkdir(parents=True, exist_ok=True)
    KEYSTORE.write_bytes(pkcs12.serialize_key_and_certificates(
        ALIAS, key, certificate, None, encryption))
    return certificate.fingerprint(hashes.SHA256()).hex()


def main():
    parser = argparse.ArgumentParser(description='Make the key Grabbit APKs are signed with.')
    parser.add_argument('--upload', action='store_true',
                        help='put the existing key back into GitHub instead of making one')
    args = parser.parse_args()

    if args.upload:
        if not KEYSTORE.exists():
            fail(f'there is no key at {KEYSTORE} to upload')
        upload(KEYSTORE)
        return

    if KEYSTORE.exists():
        fail(f'a key already exists at {KEYSTORE}. Replacing it would strand every '
             'installed copy; use --upload to put it back into GitHub instead.')
    fingerprint = make()
    PIN.write_text(fingerprint + '\n', encoding='ascii')
    print(f'made {KEYSTORE}')
    print(f'wrote {PIN.relative_to(HERE.parents[1])}: {fingerprint}')
    upload(KEYSTORE)
    print(f'\nBACK UP {KEYSTORE}. Without it no installed copy of Grabbit can be updated.')


if __name__ == '__main__':
    main()
