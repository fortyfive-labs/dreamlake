"""Read-only explicit pass-store preview; no upload or OTP generation."""
import os
from pathlib import Path
import subprocess
import threading
import re
import stat
from .pass_otp import preview_pass_otp
from .vault import resolve_selector


class PassStore:
    def sync(self, *, store, otp=False, dry_run=False, prefix="", gpg_home=None, decryptor=None):
        if not otp or not dry_run:
            raise ValueError("Only otp=True, dry_run=True preview is implemented; no upload is available")
        if not store:
            raise ValueError("An explicit pass store directory is required")
        if prefix:
            resolve_selector("probe", prefix)
        root = Path(os.path.abspath(store))
        if root.is_symlink() or not root.is_dir() or root.resolve() != root:
            raise ValueError("Pass store must be a real directory without symlink ancestors")
        entries = []

        def decrypt(ciphertext):
            if decryptor is not None:
                return decryptor(ciphertext)
            args = ["gpg", "--no-options", "--batch", "--no-tty", "--pinentry-mode", "error"]
            if gpg_home:
                args += ["--homedir", str(gpg_home)]
            # No pass extension or config execution. Timeout never falls back to pinentry.
            with subprocess.Popen(args + ["--decrypt"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
                chunks, failed = [], []
                def feed():
                    try:
                        proc.stdin.write(ciphertext)
                        proc.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass
                def drain():
                    size = 0
                    while True:
                        chunk = proc.stdout.read(16384)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > 1048576:
                            failed.append(True)
                            proc.kill()
                            break
                        chunks.append(chunk)
                writer, reader = threading.Thread(target=feed), threading.Thread(target=drain)
                writer.start()
                reader.start()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    failed.append(True)
                    proc.kill()
                    proc.wait()
                finally:
                    writer.join()
                    reader.join()
                if failed or proc.returncode:
                    raise ValueError("Credential decryption failed")
                return b"".join(chunks).decode("utf-8")

        def walk(directory):
            for source in sorted(directory.iterdir()):
                if source.name == ".git":
                    continue
                if source.is_symlink():
                    raise ValueError("Symlinks are not supported in pass preview")
                if source.is_dir():
                    walk(source)
                    continue
                if source.suffix != ".gpg":
                    continue
                if len(entries) >= 10000:
                    raise ValueError("OTP preview exceeds record limit")
                if source.resolve() != source or not source.is_file():
                    raise ValueError("Invalid pass store file")
                fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    current = source.lstat()
                    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino) or opened.st_size > 1048576 or source.resolve() != source:
                        raise ValueError("Invalid pass store file")
                    ciphertext = stream.read(1048577)
                    after = source.lstat()
                    if len(ciphertext) > 1048576 or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino) or source.resolve() != source:
                        raise ValueError("Pass store changed or exceeds size limit")
                path = str(source.relative_to(root))[:-4]
                try:
                    content = decrypt(ciphertext)
                    if not isinstance(content, str) or len(content) > 1048576:
                        raise ValueError()
                    entries.extend(preview_pass_otp([dict(path=path, content=content)])["entries"])
                except Exception:
                    entries.append(dict(path=path, status="decrypt-failed"))
        walk(root)
        for entry in entries:
            if entry["status"] == "ready" and not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", entry["path"]):
                entry["status"] = "mapping-required"
        return dict(dryRun=True, uploaded=False, prefix=prefix, entries=entries,
                    mappingRequired=sum(e["status"] == "mapping-required" for e in entries),
                    ready=sum(e["status"] == "ready" for e in entries),
                    invalid=sum(e["status"] == "invalid" for e in entries),
                    skipped=sum(e["status"] == "no-otp" for e in entries),
                    failed=sum(e["status"] == "decrypt-failed" for e in entries))
