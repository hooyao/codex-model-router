"""Run no-model boundary probes against the pinned Linux Codex package."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import socket
import stat
import subprocess
import threading
import uuid
from pathlib import Path

try:
    import grp
    import pwd
except ImportError:  # Windows imports validators for offline unit tests.
    grp = None
    pwd = None


RUNTIME = Path("/opt/cmr-codex-linux-v0.144.1/runtime")
CODEX = RUNTIME / "bin" / "codex"
HOME = Path("/var/lib/cmr-codex-probe")
CODEX_HOME = HOME / ".codex"
PROFILE = CODEX_HOME / "config.toml"
WORKSPACE = HOME / "workspace"
OUTSIDE_WRITABLE = HOME / "outside-writable"
PROTECTED = Path("/var/lib/cmr-codex-probe-protected")
PE_COPY = WORKSPACE / "benign-cmd.exe"
SOCKETS = (PROTECTED / "host-service-canary.sock",
           WORKSPACE / "host-service-canary.sock")
EXPECTED_PACKAGE_SHA256 = "3fd50cf96809b1eea294bbfba0a5c3a576871b4876a1f0e91226e520c1923be1"
EXPECTED_FILES = {
    "bin/codex": "a96f944d1a596dbfb7fdd84f482be5c50e34b04bb371126840d873e4ebf26902",
    "bin/codex-code-mode-host": "107cc233a8d90a545ee9647c527c1161068512880f1ee4eda2415eb7d33a700b",
    "codex-package.json": "aca661373fdc74d51a5a60bb3d6a258943dd326b7ab76bb01fdc18275137548d",
    "codex-path/rg": "ebeaf56f8a25e102e9419933423738b3a2a613a444fd749d695e15eba53f71f2",
    "codex-resources/bwrap": "77360cb751ccedc5971391444ac86a8a33c15b04d6b4a6fe45f5d25496e62c4c",
    "codex-resources/zsh/bin/zsh": "67faaaa89242c4a332e16e508a1977cffc24bf7fca31d4411cdfd101f3831ef3",
}
EXPECTED_OUTCOMES = {"network": "network-denied", "protected_reads": "reads-denied",
                     "writes": "writes-bounded", "nested": "nested-bounded",
                     "wsl_interop": "interop-denied"}
NAMESPACE_PATTERN = re.compile(r"^(mnt|net|pid):\[[0-9]+\]$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_tree_sha256() -> str:
    digest = hashlib.sha256()
    for path in sorted(RUNTIME.rglob("*"), key=lambda item: item.relative_to(RUNTIME).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(RUNTIME).as_posix()
        digest.update(relative.encode() + b"\0")
        digest.update(oct(path.stat().st_mode & 0o7777).encode() + b"\0")
        digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def invocation_projection() -> dict:
    environment = {"HOME": str(HOME), "CODEX_HOME": str(CODEX_HOME),
                   "USER": "cmrprobe", "LOGNAME": "cmrprobe", "SHELL": "/bin/sh",
                   "TMPDIR": str(HOME / "tmp"),
                   "PATH": f"{RUNTIME / 'bin'}:/usr/bin:/bin"}
    return {"identity": "cmrprobe", "environment": environment,
            "sandbox_prefix": [str(CODEX), "sandbox", "-P", "cmr-offline",
                               "-C", str(WORKSPACE), "--"]}


def run_as_probe(args: list[str], *, expected: int | None = 0) -> subprocess.CompletedProcess[str]:
    projection = invocation_projection()
    environment = ["env", "-i", *(f"{key}={value}" for key, value in
                                    projection["environment"].items())]
    result = subprocess.run(["runuser", "-u", "cmrprobe", "--", *environment, *args],
                            capture_output=True, text=True)
    if expected is not None and result.returncode != expected:
        raise RuntimeError(
            f"probe command returned {result.returncode}, expected {expected}: "
            f"stderr={result.stderr.strip()!r} stdout={result.stdout.strip()!r}")
    return result


def sandbox(shell: str) -> subprocess.CompletedProcess[str]:
    return run_as_probe([str(CODEX), "sandbox", "-P", "cmr-offline",
                         "-C", str(WORKSPACE), "/bin/sh", "-c", shell])


def validate_namespace_evidence(outside: list[str], inside: list[str]) -> None:
    if len(outside) != 3 or len(inside) < 5:
        raise ValueError("namespace evidence has the wrong shape")
    for index, kind in enumerate(("mnt", "net", "pid")):
        if not NAMESPACE_PATTERN.fullmatch(outside[index]) or not outside[index].startswith(kind + ":["):
            raise ValueError(f"invalid outside {kind} namespace: {outside[index]!r}")
        if not NAMESPACE_PATTERN.fullmatch(inside[index]) or not inside[index].startswith(kind + ":["):
            raise ValueError(f"invalid inside {kind} namespace: {inside[index]!r}")
        if inside[index] == outside[index]:
            raise ValueError(f"sandbox did not isolate the {kind} namespace")
    if inside[3:5] != ["NoNewPrivs:\t1", "Seccomp:\t2"]:
        raise ValueError(f"unexpected no-new-privileges/seccomp state: {inside[3:5]}")


def validate_outcomes(outcomes: dict) -> None:
    for name, expected in EXPECTED_OUTCOMES.items():
        if outcomes.get(name) != expected:
            raise ValueError(f"permission route {name} produced {outcomes.get(name)!r}, expected {expected!r}")


def validate_socket_results(results: dict) -> None:
    if set(results) != {"protected", "workspace"}:
        raise ValueError("both protected and workspace socket controls are required")
    for name, result in results.items():
        if not result.get("attempted"):
            raise ValueError(f"socket control {name} was not attempted")
        if not result.get("outside_connectable"):
            raise ValueError(f"socket control {name} lacks an outside positive control")
        if not result.get("inside_denied"):
            raise ValueError(f"socket control {name} was reachable inside the sandbox")


def copied_pe_verdict(outside_state: str, inside_state: str) -> dict:
    if inside_state != "pe-denied":
        raise ValueError("copied PE execution was not denied inside the sandbox")
    if outside_state == "executable":
        return {"outside": outside_state, "inside": inside_state, "verdict": "PASS"}
    if outside_state == "not-executable":
        return {"outside": outside_state, "inside": inside_state, "verdict": "UNKNOWN",
                "reason": "The copied PE is non-executable outside the sandbox."}
    raise ValueError(f"unknown copied PE outside control: {outside_state}")


def validate_install() -> dict:
    package = RUNTIME.parent / "codex-package-x86_64-unknown-linux-musl.tar.gz"
    if sha256(package) != EXPECTED_PACKAGE_SHA256:
        raise ValueError("package SHA-256 mismatch")
    hashes = {}
    for relative, expected in EXPECTED_FILES.items():
        path = RUNTIME / relative
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"runtime file hash mismatch: {relative}")
        metadata = path.lstat()
        expected_mode = 0o444 if relative == "codex-package.json" else 0o555
        if path.is_symlink() or metadata.st_uid != 0 or metadata.st_gid != 0 or \
                (metadata.st_mode & 0o7777) != expected_mode:
            raise ValueError(f"runtime ownership/mode mismatch: {relative}")
        hashes[relative] = actual
    if (RUNTIME.parent.lstat().st_mode & 0o7777) != 0o555:
        raise ValueError("runtime installation root is not read-only")
    return {"package_sha256": EXPECTED_PACKAGE_SHA256, "files": hashes,
            "runtime_tree_sha256": runtime_tree_sha256()}


class SocketCanaries:
    def __init__(self) -> None:
        self.servers: list[socket.socket] = []
        self.received: list[str] = []
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def __enter__(self) -> "SocketCanaries":
        for path in SOCKETS:
            if os.path.lexists(path):
                raise FileExistsError(f"socket canary already exists: {path}")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(path))
            os.chmod(path, 0o777)
            server.listen(4)
            server.settimeout(0.2)
            self.servers.append(server)
            thread = threading.Thread(target=self._serve, args=(server,), daemon=True)
            thread.start()
            self.threads.append(thread)
        return self

    def _serve(self, server: socket.socket) -> None:
        while not self.stop.is_set():
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                if self.stop.is_set():
                    return
                raise
            with connection:
                self.received.append(connection.recv(256).decode("ascii", errors="replace"))

    def __exit__(self, *_: object) -> None:
        self.stop.set()
        for server in self.servers:
            server.close()
        for thread in self.threads:
            thread.join(timeout=1)
        for path in SOCKETS:
            if path.is_socket():
                path.unlink()


def socket_inventory() -> list[str]:
    observed = []
    for root in (Path("/run"), Path("/mnt/wslg"), PROTECTED, WORKSPACE):
        for directory, _, files in os.walk(root):
            for name in files:
                path = Path(directory) / name
                try:
                    if stat.S_ISSOCK(path.lstat().st_mode):
                        observed.append(str(path))
                except OSError:
                    continue
    return sorted(set(observed))


def outside_positive_controls(marker: str) -> dict:
    run_as_probe(["/bin/sh", "-c",
                  "cat /var/lib/cmr-codex-probe-protected/credentials.json >/dev/null && "
                  "cat /var/lib/cmr-codex-probe-protected/verifier.bin >/dev/null"])
    outside_path = OUTSIDE_WRITABLE / f"outside-{marker}.txt"
    quoted = shlex.quote(str(outside_path))
    run_as_probe(["/bin/sh", "-c", f"set -eu; test ! -e {quoted}; printf outside > {quoted}; "
                  f"test \"$(cat {quoted})\" = outside; rm -- {quoted}"])
    run_as_probe(["/usr/bin/curl", "-fsS", "--max-time", "5", "https://example.com",
                  "-o", "/dev/null"])
    interop = run_as_probe(["/mnt/c/Windows/System32/cmd.exe", "/c",
                            "echo", "CMR_INTEROP_OUTSIDE_OK"]).stdout
    if "CMR_INTEROP_OUTSIDE_OK" not in interop.replace("\x00", ""):
        raise RuntimeError("outside WSL interop positive control failed")
    copied = run_as_probe([str(PE_COPY), "/c", "echo", "CMR_PE_OUTSIDE_OK"], expected=None)
    copied_state = "executable" if copied.returncode == 0 and \
        "CMR_PE_OUTSIDE_OK" in copied.stdout.replace("\x00", "") else "not-executable"
    return {"dummy_reads": "readable", "outside_write": "writable",
            "network": "reachable", "wsl_interop": "executable",
            "copied_pe": copied_state}


def socket_client(path: Path, message: str) -> str:
    return ("import socket; s=socket.socket(socket.AF_UNIX); "
            f"s.connect({str(path)!r}); s.sendall({message.encode('ascii')!r}); s.close()")


def identity_projection() -> dict:
    if grp is None or pwd is None:
        raise RuntimeError("identity projection requires POSIX account APIs")
    user = pwd.getpwnam("cmrprobe")
    group_ids = sorted(os.getgrouplist(user.pw_name, user.pw_gid))
    return {"name": user.pw_name, "uid": user.pw_uid, "gid": user.pw_gid,
            "home": user.pw_dir, "shell": user.pw_shell,
            "groups": [{"gid": gid, "name": grp.getgrgid(gid).gr_name} for gid in group_ids]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    install = validate_install()
    version = run_as_probe([str(CODEX), "--version"]).stdout.strip()
    if version != "codex-cli 0.144.1":
        raise ValueError(f"unexpected Codex version: {version}")
    sandbox("/bin/true")

    marker = uuid.uuid4().hex
    workspace_marker = WORKSPACE / f"probe-write-{marker}.txt"
    outside_namespace = run_as_probe(["/bin/sh", "-c",
                                      "readlink /proc/self/ns/mnt; readlink /proc/self/ns/net; "
                                      "readlink /proc/self/ns/pid"]).stdout.splitlines()
    with SocketCanaries() as canaries:
        outside_controls = outside_positive_controls(marker)
        inventory = socket_inventory()
        outcomes = {}
        outcomes["network"] = sandbox(
            "if /usr/bin/curl -fsS --max-time 3 https://example.com >/dev/null 2>&1; "
            "then exit 91; else echo network-denied; fi").stdout.strip()
        outcomes["protected_reads"] = sandbox(
            "for p in /var/lib/cmr-codex-probe-protected/credentials.json "
            "/var/lib/cmr-codex-probe-protected/verifier.bin; do "
            "if /bin/cat \"$p\" >/dev/null 2>&1; then exit 92; fi; done; echo reads-denied").stdout.strip()
        marker_name = shlex.quote(workspace_marker.name)
        outcomes["writes"] = sandbox(
            f"set -eu; test ! -e {marker_name}; printf {marker} > {marker_name}; "
            f"test \"$(cat {marker_name})\" = {marker}; "
            "if printf bad > /var/lib/cmr-codex-probe/outside-writable/escape.txt 2>/dev/null; "
            "then exit 93; fi; echo writes-bounded").stdout.strip()
        outcomes["nested"] = sandbox(
            "set -e; /bin/sh -c 'if cat /var/lib/cmr-codex-probe-protected/credentials.json "
            ">/dev/null 2>&1; then exit 94; fi; "
            "if curl -fsS --max-time 3 https://example.com >/dev/null 2>&1; then exit 95; fi'; "
            "echo nested-bounded").stdout.strip()
        namespace_output = sandbox(
            "readlink /proc/self/ns/mnt; readlink /proc/self/ns/net; readlink /proc/self/ns/pid; "
            "grep -E '^(NoNewPrivs|Seccomp):' /proc/self/status").stdout.splitlines()
        validate_namespace_evidence(outside_namespace, namespace_output)
        outcomes["wsl_interop"] = sandbox(
            "if /mnt/c/Windows/System32/cmd.exe /c echo CMR_INTEROP_SUCCEEDED >/dev/null 2>&1; "
            "then exit 96; else echo interop-denied; fi").stdout.strip()
        pe_inside = sandbox(
            "if ./benign-cmd.exe /c echo CMR_PE_SUCCEEDED >/dev/null 2>&1; "
            "then exit 97; else echo pe-denied; fi").stdout.strip()
        pe_result = copied_pe_verdict(outside_controls["copied_pe"], pe_inside)
        socket_results = {}
        for name, path in zip(("protected", "workspace"), SOCKETS, strict=True):
            run_as_probe(["/usr/bin/python3", "-c", socket_client(path, f"outside-{name}")])
            denied = sandbox(
                f"if /usr/bin/python3 -c {shlex.quote(socket_client(path, f'inside-{name}'))} "
                ">/dev/null 2>&1; then exit 98; else echo socket-denied; fi").stdout.strip()
            socket_results[name] = {"path": str(path), "attempted": True,
                                    "outside_connectable": True,
                                    "inside_denied": denied == "socket-denied"}
            if not socket_results[name]["inside_denied"]:
                raise RuntimeError(f"sandbox reached the {name} Unix-socket canary")
        visible_command = "for p in " + " ".join(shlex.quote(path) for path in inventory) + \
            "; do test -S \"$p\" && echo \"$p\" || true; done"
        visible_sockets = sandbox(visible_command).stdout.splitlines() if inventory else []
        validate_outcomes(outcomes)
        validate_socket_results(socket_results)
        if any(message.startswith("inside-") for message in canaries.received):
            raise RuntimeError("a sandbox process reached a host Unix-socket canary")

    negative_controls = {}
    try:
        validate_namespace_evidence(outside_namespace, [*outside_namespace,
                                                        "NoNewPrivs:\t1", "Seccomp:\t2"])
    except ValueError:
        negative_controls["unconfined_projection_rejected"] = True
    else:
        raise RuntimeError("unconfined namespace projection was not rejected")
    widened = dict(outcomes)
    widened["network"] = "network-reachable"
    try:
        validate_outcomes(widened)
    except ValueError:
        negative_controls["permission_widening_rejected"] = True
    else:
        raise RuntimeError("permission-widened projection was not rejected")
    short_circuit = {name: dict(value) for name, value in socket_results.items()}
    short_circuit["workspace"]["inside_denied"] = False
    try:
        validate_socket_results(short_circuit)
    except ValueError:
        negative_controls["socket_second_route_allowed_rejected"] = True
    else:
        raise RuntimeError("first-denied/second-allowed socket projection was not rejected")

    interop_config = Path("/proc/sys/fs/binfmt_misc/WSLInterop").read_text(encoding="ascii")
    result = {
        "schema_version": 2, "status": "PASS" if pe_result["verdict"] == "PASS" else "PARTIAL",
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "version": version, "install": install, "identity": identity_projection(),
        "bindings": {"script_sha256": sha256(Path(__file__)),
                     "profile_sha256": sha256(PROFILE),
                     "runtime_tree_sha256": install["runtime_tree_sha256"],
                     "benign_pe_sha256": sha256(PE_COPY),
                     "invocation": invocation_projection()},
        "outside_positive_controls": outside_controls,
        "probes": {**outcomes, "copied_pe": pe_result, "unix_sockets": socket_results,
                   "isolation": {"outside": outside_namespace,
                                               "inside": namespace_output},
                   "workspace_marker": workspace_marker.name,
                   "host_socket_inventory": inventory,
                   "sandbox_visible_host_sockets": visible_sockets},
        "negative_controls": negative_controls,
        "wsl_interop_configuration": interop_config.splitlines(),
    }
    if args.output.exists() and not args.replace:
        raise FileExistsError(f"output already exists: {args.output}")
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    if temporary.exists():
        raise FileExistsError(f"temporary output already exists: {temporary}")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
