r"""Publish a Grabbit release - the one both apps then offer as an update.

    build\release.ps1                         publish the version in app\grabbit\__init__.py
    build\release.ps1 --version 1.3.0         set a new version first, then publish it
    build\release.ps1 --notes "What's new"    with release notes (or --notes-file notes.md)
    build\release.ps1 --no-build              use the Windows build already in dist\Grabbit
    build\release.ps1 --dry-run               everything except changing anything on GitHub

Needs GitHub's command-line tool, signed in (winget install GitHub.cli, then
gh auth login), and the build venv from build\bootstrap.ps1.

What it does:

  1. Checks the work is committed and pushed, and that this version is newer
     than the latest release. --version writes the new number into the app,
     commits it and pushes it.
  2. Runs ui_check.py and update_check.py: a release that fails its own checks
     is not one to publish.
  3. Builds the Windows app (build_app.ps1), checks the exe carries the version,
     and zips it.
  4. Gets the APK for this exact commit from the Android workflow - an existing
     run if there is one, otherwise it starts one and waits for it.
  5. Reads the APK's signature and refuses it unless it was signed with the
     release key, whose fingerprint build\android\signing-sha256.txt pins. An
     APK signed with anything else would install on no phone that has Grabbit.
  6. Writes a .sha256 beside each file, shows what it is about to publish,
     asks, and creates the release: tag vX.Y.Z on this commit, both files and
     their checksums.

The Android key is ~\.grabbit\android\release.keystore (make_signing_key.py).
BACK IT UP: lose it and no installed copy can ever be updated again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))

from grabbit import ed25519, selfupdate, updates    # noqa: E402

REPO = updates.REPO
WORKFLOW = 'android.yml'
DIST = ROOT / 'dist'
PIN = ROOT / 'build' / 'android' / 'signing-sha256.txt'
PYTHON = Path(sys.executable)

# The key each release's manifest is signed with, and where the apps keep its
# public half. Homework Hub keeps its own at ~/.homework-hub/release.
UPDATE_KEY = Path.home() / '.grabbit' / 'release' / 'update-signing.key'
RELEASE_KEY_MODULE = ROOT / 'app' / 'grabbit' / 'release_key.py'


def say(message: str) -> None:
    print(f'\n>> {message}', flush=True)


def fail(message: str):
    print(f'\nerror: {message}', file=sys.stderr, flush=True)
    sys.exit(1)


def ask(question: str) -> bool:
    try:
        return input(f'{question} [y/N] ').strip().lower() in ('y', 'yes')
    except EOFError:
        return False


def run(*args, capture: bool = True, check: bool = True, cwd: Path = ROOT, **kwargs):
    result = subprocess.run([str(a) for a in args], cwd=cwd, text=True,
                            capture_output=capture, encoding='utf-8', errors='replace', **kwargs)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip() if capture else ''
        fail(f'{Path(str(args[0])).name} {" ".join(str(a) for a in args[1:3])} failed'
             + (f':\n{detail}' if detail else ''))
    return result


def find_gh() -> str:
    found = shutil.which('gh') or next(
        (str(p) for p in (Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'GitHub CLI' / 'gh.exe',
                          Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'GitHub CLI' / 'gh.exe')
         if p.is_file()), None)
    if not found:
        fail("GitHub's command-line tool is not installed: winget install GitHub.cli, then gh auth login")
    if subprocess.run([found, 'auth', 'status'], capture_output=True).returncode != 0:
        fail('gh is not signed in: run gh auth login')
    return found


GH = ''


def gh(*args, **kwargs):
    return run(GH, *args, **kwargs)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksum(path: Path) -> Path:
    target = path.with_name(path.name + '.sha256')
    target.write_bytes(f'{sha256_of(path)}  {path.name}\r\n'.encode('ascii'))
    return target


# ------------------------------------------------------------------ version

def project_version() -> str:
    text = (ROOT / 'app' / 'grabbit' / '__init__.py').read_text(encoding='utf-8')
    match = re.search(r"^APP_VERSION = '([^']+)'", text, re.M)
    if not match:
        fail('could not find APP_VERSION in app/grabbit/__init__.py')
    return match.group(1)


def rewrite(path: Path, pattern: str, replacement: str, count: int) -> None:
    """Edit a file in place, leaving its line endings exactly as they were."""
    raw = path.read_bytes().decode('utf-8')
    new, made = re.subn(pattern, replacement, raw, flags=re.M)
    if made != count:
        fail(f'expected {count} version number(s) in {path.relative_to(ROOT)}, found {made}')
    path.write_bytes(new.encode('utf-8'))


def set_project_version(version: str) -> None:
    major, minor, patch = version.split('.')
    rewrite(ROOT / 'app' / 'grabbit' / '__init__.py',
            r"^APP_VERSION = '[^']+'", f"APP_VERSION = '{version}'", 1)
    info = ROOT / 'build' / 'version_info.txt'
    rewrite(info, r'(filevers|prodvers)=\(\d+, \d+, \d+, \d+\)',
            rf'\1=({major}, {minor}, {patch}, 0)', 2)
    rewrite(info, r"(StringStruct\('(?:FileVersion|ProductVersion)', ')[\d.]+(')",
            rf'\g<1>{version}.0\g<2>', 2)


def latest_published() -> str | None:
    result = gh('release', 'view', '--repo', REPO, '--json', 'tagName', check=False)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)['tagName']


def tag_exists(tag: str) -> bool:
    return gh('release', 'view', tag, '--repo', REPO, check=False).returncode == 0 or bool(
        run('git', 'ls-remote', '--tags', 'origin', f'refs/tags/{tag}').stdout.strip())


# ------------------------------------------------------------ update signing

def recorded_public_key() -> str:
    """The public key the apps carry - read from the file, not imported, so a
    key made a moment ago is the one seen."""
    if not RELEASE_KEY_MODULE.exists():
        return ''
    match = re.search(r"^PUBLIC_KEY = '([0-9a-f]*)'", RELEASE_KEY_MODULE.read_text(encoding='utf-8'), re.M)
    return match.group(1) if match else ''


def load_update_key() -> str:
    """The signing secret, checked against the public key every build carries."""
    recorded = recorded_public_key()
    if not UPDATE_KEY.exists():
        if recorded:
            fail(f'the update-signing key is missing: {UPDATE_KEY}\n\nEvery copy of Grabbit '
                 'only installs updates signed with it. Restore it from your backup.')
        fail('there is no update-signing key yet: run release.ps1 --make-update-key once, '
             'then commit app/grabbit/release_key.py')
    secret = UPDATE_KEY.read_text(encoding='ascii').strip().lower()
    if not re.fullmatch(r'[0-9a-f]{64}', secret):
        fail(f'{UPDATE_KEY} is not a signing key')
    if ed25519.public_key(bytes.fromhex(secret)).hex() != recorded:
        fail(f'{UPDATE_KEY} does not match the public key in app/grabbit/release_key.py. '
             'Copies out there only accept updates signed with the matching key: restore '
             'it from your backup.')
    return secret


def make_update_key() -> int:
    if UPDATE_KEY.exists() or recorded_public_key():
        fail('an update-signing key already exists. Replacing it would leave every '
             'installed copy refusing all later updates.')
    secret = ed25519.generate_secret().hex()
    public = ed25519.public_key(bytes.fromhex(secret)).hex()
    UPDATE_KEY.parent.mkdir(parents=True, exist_ok=True)
    UPDATE_KEY.write_text(secret + '\n', encoding='ascii')
    RELEASE_KEY_MODULE.write_bytes((
        '"""The public half of the key Grabbit\'s releases are signed with.\n\n'
        'Written once by build/release.py --make-update-key. The private half stays on\n'
        'the machine that publishes, at ~/.grabbit/release/update-signing.key, and every\n'
        'copy of Grabbit installs only updates whose manifest it signed (updates.py).\n'
        'Changing this line strands every installed copy: they would refuse all later\n'
        'updates.\n"""\n\n'
        f"PUBLIC_KEY = '{public}'\n").encode('ascii'))
    print(f'made {UPDATE_KEY}')
    print(f'wrote {RELEASE_KEY_MODULE.relative_to(ROOT)} - commit it; every build must carry it')
    print(f'\nBACK IT UP, with ~/.grabbit/android/release.keystore. Without them no installed '
          'copy of Grabbit can be updated again.')
    return 0


def sign_release(version: str, notes: str, archive: Path, apk: Path, secret: str) -> list:
    """Write latest.json and its signature, and prove the apps will believe them."""
    published = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    manifest = updates.build_manifest(version, notes=notes, published_at=published,
                                      desktop=archive, android=apk)
    signature = updates.sign_manifest(manifest, secret)
    try:
        release = updates.verify_and_parse(manifest, signature, key=recorded_public_key())
    except updates.UpdateError as error:
        fail(f'the signed manifest does not verify: {error}')
    if release.version != version or release.desktop is None or release.android is None:
        fail('the signed manifest does not describe this release')
    manifest_path = DIST / updates.MANIFEST_NAME
    signature_path = DIST / updates.SIGNATURE_NAME
    manifest_path.write_bytes(manifest)
    signature_path.write_bytes(signature.encode('ascii') + b'\n')
    print(f'   {manifest_path.name} signed; the apps\' own check accepts it')
    return [manifest_path, signature_path]


# -------------------------------------------------------------------- state

def require_clean_and_pushed() -> str:
    if run('git', 'status', '--porcelain').stdout.strip():
        fail('there are uncommitted changes - commit or stash them first, so the release '
             'is exactly a commit anyone can check out')
    run('git', 'fetch', '--quiet', 'origin', 'main')
    head = run('git', 'rev-parse', 'HEAD').stdout.strip()
    if head != run('git', 'rev-parse', 'origin/main').stdout.strip():
        fail('this commit is not what origin/main points at - push it (or pull) first, '
             'so the Android workflow can build it')
    return head


def run_checks() -> None:
    for script in ('ui_check.py', 'update_check.py'):
        say(f'checks: {script}')
        result = run(PYTHON, ROOT / 'build' / script, check=False,
                     env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
        last = [line for line in result.stdout.splitlines() if line.strip()][-1:]
        print(f'   {last[0].strip() if last else "(no output)"}')
        if result.returncode != 0:
            print(result.stdout)
            fail(f'{script} failed - fix it before releasing')


# ------------------------------------------------------------------ windows

def exe_version(exe: Path) -> str:
    result = run('powershell', '-NoProfile', '-Command',
                 f"(Get-Item -LiteralPath '{exe}').VersionInfo.FileVersion", check=False)
    return result.stdout.strip()


def build_windows(version: str, head: str, build: bool) -> Path:
    if build:
        say('building the Windows app (a few minutes)')
        run('powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            ROOT / 'build' / 'build_app.ps1', capture=False)
    folder = DIST / 'Grabbit'
    exe = folder / 'Grabbit.exe'
    if not exe.is_file():
        fail(f'{exe} is missing - run without --no-build')
    found = exe_version(exe)
    if found != f'{version}.0':
        fail(f'{exe.name} says it is {found or "no version"}, not {version} - '
             + ('the build did not pick up the new number' if build else 'run without --no-build'))
    # The version number alone cannot tell a build of this commit from an
    # older one that carries the same number, so build_app.ps1 records which
    # commit it built.
    stamp = folder / 'build-commit.txt'
    built = stamp.read_text(encoding='ascii').strip() if stamp.exists() else ''
    if built != head:
        fail(f'dist\\Grabbit was built from {built[:12] or "an unrecorded commit"}, '
             f'not {head[:12]} - run without --no-build')

    # Everything it hands work to. build_app.ps1 only warns when one is missing,
    # which is fine for a quick build and not for one going to everyone.
    missing = [tool for tool in ('aria2c.exe', 'ffmpeg/ffmpeg.exe', 'ffmpeg/ffprobe.exe',
                                 'deno/deno.exe', 'gallery-dl.exe')
               if not (folder / 'tools' / tool).is_file()]
    if missing:
        fail('the build is missing ' + ', '.join(missing) + ' - see the warnings from build_app.ps1')
    # The updater swaps only a folder holding what selfupdate.SHIPPED lists, so
    # a build with anything more would never update itself again.
    extra = sorted(p.name for p in folder.iterdir() if p.name not in selfupdate.SHIPPED)
    if extra:
        fail(f'dist\\Grabbit holds {", ".join(extra)}, which selfupdate.SHIPPED does not list - '
             'add it there, or copies with it could never update themselves')
    # And that it starts: the same --self-test the updater will ask of it on
    # every machine it reaches, asked here first.
    answer = Path(tempfile.gettempdir()) / f'grabbit-release-self-test-{os.getpid()}.txt'
    answer.unlink(missing_ok=True)
    try:
        subprocess.run([str(exe), '--self-test', str(answer)], timeout=120)
        said = answer.read_text(encoding='utf-8').strip() if answer.exists() else ''
    except subprocess.TimeoutExpired:
        said = ''
    finally:
        answer.unlink(missing_ok=True)
    if said != version:
        fail(f'{exe.name} does not start (--self-test answered {said or "nothing"}), '
             'so it cannot be released')
    print(f'   {exe.name} starts, and says it is {said}')

    say('zipping it')
    archive = DIST / f'Grabbit-{version}-win64.zip'
    part = archive.with_name(archive.name + '.part')
    with zipfile.ZipFile(part, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(p for p in folder.rglob('*') if p.is_file()):
            zf.write(path, Path('Grabbit') / path.relative_to(folder))
    os.replace(part, archive)
    with zipfile.ZipFile(archive) as zf:
        if zf.testzip() is not None:
            fail(f'{archive.name} did not come out whole')
    print(f'   {archive.name}  {archive.stat().st_size / 1e6:.1f} MB')
    return archive


# ------------------------------------------------------------------ android

def apk_signer_sha256(path: Path) -> str:
    """SHA-256 of the certificate an APK is signed with, read from its APK
    Signing Block (scheme v2 or v3) - what `apksigner verify --print-certs`
    reports, without needing Java or the Android SDK here."""
    data = path.read_bytes()
    eocd = data.rfind(b'PK\x05\x06')
    if eocd < 0:
        raise ValueError('not a zip file')
    central = struct.unpack_from('<I', data, eocd + 16)[0]
    if data[central - 16:central] != b'APK Sig Block 42':
        raise ValueError('it has no APK signing block')
    # The block ends with its size (not counting the size field at its start)
    # and the magic; its ID-value pairs run from just after that first field
    # up to the second.
    size = struct.unpack_from('<Q', data, central - 24)[0]
    position, end = central - size, central - 24

    def prefixed(buffer, offset):
        length = struct.unpack_from('<I', buffer, offset)[0]
        return buffer[offset + 4:offset + 4 + length], offset + 4 + length

    while position < end:
        length, block = struct.unpack_from('<QI', data, position)
        if block in (0x7109871a, 0xf05368c0):          # signature schemes v2 and v3
            value = data[position + 12:position + 8 + length]
            signers, _ = prefixed(value, 0)
            signer, _ = prefixed(signers, 0)
            signed, _ = prefixed(signer, 0)
            _digests, offset = prefixed(signed, 0)
            certificates, _ = prefixed(signed, offset)
            certificate, _ = prefixed(certificates, 0)
            return hashlib.sha256(certificate).hexdigest()
        position += 8 + length
    raise ValueError('it is not signed with scheme v2 or v3')


# What the Android workflow builds from: its push paths. A commit that changes
# none of these builds exactly the APK the commit before it did.
ANDROID_INPUTS = ('android', 'app/grabbit', 'build/android', '.github/workflows/android.yml')


def recent_runs() -> list:
    """The Android workflow's recent runs, newest first."""
    result = gh('run', 'list', '--repo', REPO, '--workflow', WORKFLOW, '--limit', '40',
                '--json', 'databaseId,headSha,status,conclusion,createdAt')
    return json.loads(result.stdout)


def workflow_runs(sha: str) -> list:
    return [r for r in recent_runs() if r['headSha'] == sha]


def same_apk_inputs(built: str, head: str) -> bool:
    """Whether building `head` would compile exactly what `built` did."""
    if run('git', 'merge-base', '--is-ancestor', built, head, check=False).returncode != 0:
        return False
    return run('git', 'diff', '--quiet', built, head, '--', *ANDROID_INPUTS,
               check=False).returncode == 0


def wait_for(run_id: int) -> None:
    started = time.monotonic()
    while True:
        state = json.loads(gh('run', 'view', str(run_id), '--repo', REPO,
                              '--json', 'status,conclusion').stdout)
        if state['status'] == 'completed':
            if state['conclusion'] != 'success':
                fail(f'the Android build failed: https://github.com/{REPO}/actions/runs/{run_id}')
            return
        minutes = (time.monotonic() - started) / 60
        print(f'   still building ({minutes:.0f} min) - https://github.com/{REPO}/actions/runs/{run_id}',
              flush=True)
        time.sleep(30)


def android_build(sha: str, dry_run: bool) -> int:
    """The run that built this commit's APK, waiting for it or starting it."""
    runs = workflow_runs(sha)
    done = [r for r in runs if r['status'] == 'completed' and r['conclusion'] == 'success']
    if done:
        return done[0]['databaseId']
    going = [r for r in runs if r['status'] != 'completed']
    if going:
        say('the Android build for this commit is still running - waiting for it')
        wait_for(going[0]['databaseId'])
        return going[0]['databaseId']
    # A commit that touched nothing the APK is made from - the README, this
    # script - starts no Android build of its own, and needs none.
    for earlier in recent_runs():
        if (earlier['status'] == 'completed' and earlier['conclusion'] == 'success'
                and same_apk_inputs(earlier['headSha'], sha)):
            say(f'reusing the APK built from {earlier["headSha"][:7]}: nothing it is made '
                'from has changed since')
            return earlier['databaseId']
    if dry_run:
        fail('no Android build exists for this commit yet, and a dry run starts nothing: '
             f'run it for real, or start one with gh workflow run {WORKFLOW}')
    say('no Android build for this commit yet - starting one (about ten minutes)')
    gh('workflow', 'run', WORKFLOW, '--repo', REPO, '--ref', 'main')
    for _ in range(30):
        time.sleep(4)
        runs = [r for r in workflow_runs(sha) if r['status'] != 'completed']
        if runs:
            wait_for(runs[0]['databaseId'])
            return runs[0]['databaseId']
    fail('started the Android build, but it never appeared - look at the Actions tab')


def fetch_apk(version: str, sha: str, dry_run: bool) -> Path:
    run_id = android_build(sha, dry_run)
    say(f'fetching the APK from run {run_id}')
    with tempfile.TemporaryDirectory(prefix='grabbit-release-') as scratch:
        gh('run', 'download', str(run_id), '--repo', REPO, '--dir', scratch)
        # The artifact arrives in a folder named after it - which also ends in
        # .apk - so files only.
        apks = [p for p in Path(scratch).rglob('*.apk') if p.is_file()]
        if len(apks) != 1:
            fail(f'expected one APK from run {run_id}, found {len(apks)}')
        built = apks[0]
        if not built.name.startswith(f'Grabbit-{version}-arm64-'):
            fail(f'the APK from run {run_id} is {built.name}, not version {version}')
        recorded = built.with_name(built.name + '.sha256')
        if recorded.exists() and recorded.read_text().split()[0] != sha256_of(built):
            fail(f'{built.name} does not match the checksum the build recorded for it')

        pinned = PIN.read_text(encoding='ascii').strip()
        try:
            signer = apk_signer_sha256(built)
        except ValueError as error:
            fail(f'could not read the APK signature: {error}')
        if signer != pinned:
            fail(f'the APK is signed by {signer[:16]}…, not the release key {pinned[:16]}… '
                 '(build/android/signing-sha256.txt). It would not install over Grabbit on '
                 'any phone - check the ANDROID_KEYSTORE_B64 secret.')
        print(f'   signed with the release key ({signer[:16]}…)')

        final = DIST / f'Grabbit-{version}-arm64.apk'
        DIST.mkdir(exist_ok=True)
        shutil.copyfile(built, final)
    print(f'   {final.name}  {final.stat().st_size / 1e6:.1f} MB')
    return final


# -------------------------------------------------------------------- notes

def default_notes(previous: str | None) -> str:
    span = f'{previous}..HEAD' if previous else 'HEAD'
    if previous:
        run('git', 'fetch', '--quiet', '--tags', 'origin', check=False)
        if run('git', 'rev-parse', '--verify', '--quiet', previous, check=False).returncode != 0:
            span = 'HEAD'
    subjects = run('git', 'log', span, '--no-merges', '--format=- %s', '-n', '60').stdout.strip()
    return 'What changed:\n\n' + (subjects or '- (no changes recorded)')


# --------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description='Publish a Grabbit release.')
    parser.add_argument('--version', help='set this version (X.Y.Z) first, commit it and push it')
    parser.add_argument('--notes', default='', help='release notes, as plain text')
    parser.add_argument('--notes-file', type=Path, help='read the release notes from a file')
    parser.add_argument('--no-build', action='store_true',
                        help='use the Windows build already in dist\\Grabbit')
    parser.add_argument('--dry-run', action='store_true',
                        help='do everything except changing anything on GitHub or in git')
    parser.add_argument('--yes', action='store_true', help='publish without asking first')
    parser.add_argument('--make-update-key', action='store_true',
                        help='make the key releases are signed with - once, ever')
    args = parser.parse_args()

    if args.make_update_key:
        return make_update_key()

    global GH
    GH = find_gh()
    os.chdir(ROOT)

    version = project_version()
    if args.version:
        if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
            fail(f'{args.version} is not a version like 1.3.0')
        if not updates.is_newer(args.version, version) and args.version != version:
            fail(f'{args.version} is not newer than {version}')
    head = require_clean_and_pushed()
    # Before anything slow: a release that cannot be signed is not worth building.
    secret = load_update_key()

    if args.version and args.version != version:
        version = args.version
        if args.dry_run:
            say(f'would set the version to {version}, commit it and push it (dry run)')
        else:
            say(f'setting the version to {version}')
            set_project_version(version)
            run('git', 'add', 'app/grabbit/__init__.py', 'build/version_info.txt')
            run('git', 'commit', '--quiet', '-m', f'Grabbit {version}')
            run('git', 'push', '--quiet', 'origin', 'HEAD:main')
            head = run('git', 'rev-parse', 'HEAD').stdout.strip()

    tag = f'v{version}'
    previous = latest_published()
    say(f'releasing Grabbit {version} from {head[:7]} (latest published: {previous or "none"})')
    if tag_exists(tag):
        fail(f'{tag} is already released - pick a newer number with --version')
    if previous and not updates.is_newer(version, previous):
        fail(f'{version} is not newer than {previous}, so no copy of Grabbit would offer it')

    # The notes go into the signed manifest - the apps show them when they ask
    # whether to update - so they are settled before anything is signed.
    if args.notes_file:
        notes = args.notes_file.read_text(encoding='utf-8')
    else:
        notes = args.notes or default_notes(previous)

    run_checks()
    files = []
    if version != project_version():
        # Only a dry run gets here: the number was never written, so neither
        # build could carry it yet.
        say(f'dry run: would build the Windows app and the APK as {version}')
    else:
        archive = build_windows(version, head, build=not args.no_build)
        apk = fetch_apk(version, head, args.dry_run)
        say('signing the release')
        files = [archive, write_checksum(archive), apk, write_checksum(apk),
                 *sign_release(version, notes, archive, apk, secret)]

    say('ready to publish')
    print(f'   tag      {tag} on {head[:7]}')
    print(f'   title    Grabbit {version}')
    for path in files:
        print(f'   file     {path.name:34s} {path.stat().st_size / 1e6:8.1f} MB')
    print('   notes    ' + notes.replace('\n', '\n            '))

    if args.dry_run:
        say('dry run - nothing was published')
        return 0
    if not args.yes and not ask(f'\nPublish Grabbit {version} to github.com/{REPO}?'):
        say('not published')
        return 1

    with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as handle:
        handle.write(notes)
    try:
        gh('release', 'create', tag, '--repo', REPO, '--target', head,
           '--title', f'Grabbit {version}', '--notes-file', handle.name, *files, capture=False)
    finally:
        os.unlink(handle.name)

    # The apps ask exactly this - fetch the manifest, check its signature - so
    # ask it too.
    answer = updates.check('windows', current='0.0.0')
    if answer.latest == version:
        say(f'published - every copy older than {version} will now offer it')
    else:
        say(f'published, but the apps would see: {answer.latest or answer.error}')
    print(f'   https://github.com/{REPO}/releases/tag/{tag}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
