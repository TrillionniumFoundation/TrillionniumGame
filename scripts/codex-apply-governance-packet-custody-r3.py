#!/usr/bin/env python3
"""Apply the one-shot descriptor-relative custody repair to PR #115."""
from pathlib import Path

source_path = Path("scripts/capture-repository-governance-readback.py")
test_path = Path("tests/control_plane/test_capture_repository_governance_readback.py")

source = source_path.read_text(encoding="utf-8")
old = '''def write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def prepare(path: Path) -> Path:
    path = path.expanduser().absolute()
    if path.exists():
        if path.is_symlink() or not path.is_dir() or any(path.iterdir()):
            raise ReadbackError("output must be a real empty directory")
    else:
        path.mkdir(mode=0o700, parents=True)
    return path
'''
new = r'''class PacketTarget:
    def __init__(self, writer: "PacketWriter", name: str):
        self.writer = writer
        self.name = name


class PacketMember:
    def __init__(self, writer: "PacketWriter", name: str):
        self.writer = writer
        self.name = name

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, PacketMember):
            return NotImplemented
        return self.name < other.name

    def read_bytes(self) -> bytes:
        return self.writer.read_verified(self.name)


class PacketWriter:
    NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    CHUNK = 64 * 1024

    def __init__(self, path: Path):
        required = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
        if os.name != "posix" or any(not hasattr(os, name) for name in required):
            raise ReadbackError("descriptor-relative packet custody is unavailable")
        absolute = path.expanduser()
        if not absolute.is_absolute():
            absolute = Path.cwd() / absolute
        absolute = absolute.absolute()
        parts = absolute.parts
        if not parts or parts[0] != "/" or len(parts) == 1:
            raise ReadbackError("output must name a non-root directory")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open("/", flags)
        try:
            for component in parts[1:]:
                if component in {"", ".", ".."}:
                    raise ReadbackError("output path contains a noncanonical component")
                try:
                    child = os.open(component, flags, dir_fd=descriptor)
                except FileNotFoundError:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    child = os.open(component, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            if os.listdir(descriptor):
                raise ReadbackError("output must be a real empty directory")
            self.path = absolute
            self.descriptor = descriptor
            self.directory_identity = os.fstat(descriptor)
            self.records: dict[str, dict[str, int | str]] = {}
            self.closed = False
        except OSError as error:
            os.close(descriptor)
            raise ReadbackError("output directory could not be securely opened") from error
        except BaseException:
            os.close(descriptor)
            raise

    def __truediv__(self, name: str) -> PacketTarget:
        return PacketTarget(self, name)

    def _require_open(self) -> None:
        if self.closed:
            raise ReadbackError("packet writer is closed")

    def _check_directory_identity(self) -> None:
        self._require_open()
        current = os.fstat(self.descriptor)
        try:
            named = os.stat(self.path, follow_symlinks=False)
        except OSError as error:
            raise ReadbackError("output directory identity is unavailable") from error
        expected = self.directory_identity
        if not os.path.isdir(self.path) or (
            current.st_dev, current.st_ino, named.st_dev, named.st_ino
        ) != (expected.st_dev, expected.st_ino, expected.st_dev, expected.st_ino):
            raise ReadbackError("output directory identity changed")

    @staticmethod
    def _identity(stat_result: os.stat_result) -> tuple[int, ...]:
        return (
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_mode,
            stat_result.st_nlink,
            stat_result.st_size,
            stat_result.st_mtime_ns,
            stat_result.st_ctime_ns,
        )

    def write(self, name: str, data: bytes) -> None:
        self._check_directory_identity()
        if not self.NAME.fullmatch(name) or name in self.records:
            raise ReadbackError("packet member name is invalid or duplicated")
        if name == "SHA256SUMS":
            expected = "".join(
                f"{record['sha256']}  {member}\n"
                for member, record in sorted(self.records.items())
            ).encode()
            if data != expected:
                raise ReadbackError("SHA256SUMS does not match verified packet members")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor = os.open(name, flags, 0o600, dir_fd=self.descriptor)
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise ReadbackError("packet member write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            stat_result = os.fstat(descriptor)
            if stat_result.st_size != len(data):
                raise ReadbackError("packet member size changed during write")
            self.records[name] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "dev": stat_result.st_dev,
                "ino": stat_result.st_ino,
                "mode": stat_result.st_mode,
                "nlink": stat_result.st_nlink,
                "mtime_ns": stat_result.st_mtime_ns,
                "ctime_ns": stat_result.st_ctime_ns,
            }
        finally:
            os.close(descriptor)
        if name == "SHA256SUMS":
            self._check_exact_member_set()
            os.fsync(self.descriptor)
            self.close()

    def _check_exact_member_set(self) -> None:
        self._check_directory_identity()
        if set(os.listdir(self.descriptor)) != set(self.records):
            raise ReadbackError("packet directory contains an untracked member")

    def iterdir(self) -> list[PacketMember]:
        self._check_exact_member_set()
        return [PacketMember(self, name) for name in self.records]

    def read_verified(self, name: str) -> bytes:
        self._check_exact_member_set()
        record = self.records.get(name)
        if record is None or name == "SHA256SUMS":
            raise ReadbackError("packet member is not available for sealing")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(name, flags, dir_fd=self.descriptor)
        try:
            before = os.fstat(descriptor)
            expected_identity = (
                record["dev"], record["ino"], record["mode"], record["nlink"],
                record["size"], record["mtime_ns"], record["ctime_ns"],
            )
            if self._identity(before) != expected_identity:
                raise ReadbackError("packet member identity changed before sealing")
            remaining = int(record["size"]) + 1
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(descriptor, min(self.CHUNK, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after = os.fstat(descriptor)
            if self._identity(after) != expected_identity:
                raise ReadbackError("packet member identity changed during sealing")
            if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ReadbackError("packet member bytes changed before sealing")
            return data
        finally:
            os.close(descriptor)

    def close(self) -> None:
        if not getattr(self, "closed", True):
            os.close(self.descriptor)
            self.closed = True

    def __del__(self) -> None:
        self.close()


def write(path: PacketTarget, data: bytes) -> None:
    if not isinstance(path, PacketTarget):
        raise ReadbackError("packet writes must use a pinned directory descriptor")
    path.writer.write(path.name, data)


def prepare(path: Path) -> PacketWriter:
    return PacketWriter(path)
'''

if source.count(old) != 1:
    raise SystemExit("expected packet write block was not found exactly once")
source = source.replace(old, new)
source = source.replace(
    "def retain(output: Path, key: str, path: str, result: tuple[int, dict[str, str], Any]) -> Any:",
    "def retain(output: PacketWriter, key: str, path: str, result: tuple[int, dict[str, str], Any]) -> Any:",
    1,
)

replacements = [
    (
        'REQUIRED_CHECK = "trillionnium-game-merge-gate"\n',
        'REQUIRED_CHECK = "trillionnium-game-merge-gate"\nREQUIRED_CHECK_APP_ID = 15368\n',
    ),
    (
        '''    context_present = REQUIRED_CHECK in (required.get("contexts", []) if isinstance(required, dict) else [])
    context_present |= any(
        isinstance(item, dict) and item.get("context") == REQUIRED_CHECK
        for item in (required.get("checks", []) if isinstance(required, dict) else [])
    )
    check_rows = checks.get("check_runs", []) if isinstance(checks, dict) else []
''',
        '''    context_present = any(
        isinstance(item, dict)
        and item.get("context") == REQUIRED_CHECK
        and item.get("app_id") == REQUIRED_CHECK_APP_ID
        for item in (required.get("checks", []) if isinstance(required, dict) else [])
    )
    check_rows = checks.get("check_runs", []) if isinstance(checks, dict) else []
    merge_gate_rows = [
        row for row in check_rows
        if isinstance(row, dict)
        and row.get("name") == REQUIRED_CHECK
        and nested(row, "app", "id") == REQUIRED_CHECK_APP_ID
        and isinstance(row.get("id"), int)
    ]
    latest_merge_gate = max(merge_gate_rows, key=lambda row: row["id"], default=None)
''',
    ),
    (
        '''        "successful_exact_main_merge_gate": any(
            isinstance(row, dict) and row.get("name") == REQUIRED_CHECK
            and row.get("status") == "completed" and row.get("conclusion") == "success"
            for row in check_rows
        ),
''',
        '''        "successful_exact_main_merge_gate": (
            isinstance(latest_merge_gate, dict)
            and latest_merge_gate.get("status") == "completed"
            and latest_merge_gate.get("conclusion") == "success"
        ),
''',
    ),
    (
        '''        isinstance(values[key], dict)
        and values[key].get("name") == name
        and any(
''',
        '''        isinstance(values[key], dict)
        and values[key].get("name") == name
        and values[key].get("prevent_self_review") is True
        and any(
''',
    ),
    (
        '''            and isinstance(values["environments"].get("environments"), list)
            and not has_next_page(reads["environments"][1])
''',
        '''            and isinstance(values["environments"].get("environments"), list)
            and values["environments"].get("total_count") == len(values["environments"]["environments"])
            and not has_next_page(reads["environments"][1])
''',
    ),
]
for before, after in replacements:
    if source.count(before) != 1:
        raise SystemExit("expected hardening source block was not found exactly once")
    source = source.replace(before, after)
source_path.write_text(source, encoding="utf-8")

tests = test_path.read_text(encoding="utf-8")
fixture_replacements = [
    (
        '{"total_count": 1, "check_runs": [{"name": MODULE.REQUIRED_CHECK, "status": "completed", "conclusion": "success"}]}',
        '{"total_count": 1, "check_runs": [{"id": 1, "name": MODULE.REQUIRED_CHECK, "app": {"id": MODULE.REQUIRED_CHECK_APP_ID}, "status": "completed", "conclusion": "success"}]}',
    ),
    (
        '{"strict": True, "contexts": [MODULE.REQUIRED_CHECK]}',
        '{"strict": True, "contexts": [MODULE.REQUIRED_CHECK], "checks": [{"context": MODULE.REQUIRED_CHECK, "app_id": MODULE.REQUIRED_CHECK_APP_ID}]}',
    ),
    (
        '{"environments": [{"name": "governance-audit"}]}',
        '{"total_count": 1, "environments": [{"name": "governance-audit"}]}',
    ),
    (
        '{"name": "governance-audit", "protection_rules": [',
        '{"name": "governance-audit", "prevent_self_review": True, "protection_rules": [',
    ),
]
for before, after in fixture_replacements:
    if tests.count(before) != 1:
        raise SystemExit("expected test fixture block was not found exactly once")
    tests = tests.replace(before, after)
marker = '\n\nif __name__ == "__main__": unittest.main()'
addition = r'''

    def test_latest_trusted_merge_gate_attempt_controls_result(self):
        data = values()
        data["main_checks"] = {
            "total_count": 3,
            "check_runs": [
                {"id": 1, "name": MODULE.REQUIRED_CHECK,
                 "app": {"id": MODULE.REQUIRED_CHECK_APP_ID},
                 "status": "completed", "conclusion": "success"},
                {"id": 2, "name": MODULE.REQUIRED_CHECK,
                 "app": {"id": MODULE.REQUIRED_CHECK_APP_ID},
                 "status": "completed", "conclusion": "failure"},
                {"id": 3, "name": MODULE.REQUIRED_CHECK,
                 "app": {"id": 999},
                 "status": "completed", "conclusion": "success"},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.capture(
                FakeApi(data), Path(directory) / "packet",
                "a" * 40, ["governance-audit"],
            )
            self.assertFalse(result["assertions"]["successful_exact_main_merge_gate"])

    def test_packet_writer_rejects_directory_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "packet"
            writer = MODULE.prepare(output)
            MODULE.write(writer / "one.json", b"safe\n")
            output.rename(Path(directory) / "moved")
            output.mkdir()
            with self.assertRaises(MODULE.ReadbackError):
                writer.iterdir()
            writer.close()

    def test_packet_writer_rejects_member_mutation_and_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "packet"
            writer = MODULE.prepare(output)
            MODULE.write(writer / "one.json", b"safe\n")
            (output / "one.json").write_bytes(b"evil\n")
            with self.assertRaises(MODULE.ReadbackError):
                writer.read_verified("one.json")
            writer.close()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "packet"
            writer = MODULE.prepare(output)
            MODULE.write(writer / "one.json", b"safe\n")
            (output / "injected").write_text("x")
            with self.assertRaises(MODULE.ReadbackError):
                writer.iterdir()
            writer.close()

    def test_packet_writer_rejects_symlinked_ancestor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(MODULE.ReadbackError):
                MODULE.prepare(alias / "packet")
'''
if tests.count(marker) != 1:
    raise SystemExit("test insertion marker was not found exactly once")
tests = tests.replace(marker, addition + marker)
test_path.write_text(tests, encoding="utf-8")
