#!/usr/bin/env python3
"""Compose reproducible per-family denominator review packets and acceptance gates."""
from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def generator_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Generate immutable, leaf-complete denominator family review packets."""
        from __future__ import annotations

        import argparse
        import hashlib
        import json
        import re
        import shutil
        from pathlib import Path
        from typing import Any, Iterable

        ROOT = Path(__file__).resolve().parents[1]
        PARITY = ROOT / "docs/development/PARITY_DENOMINATORS.json"
        WORKLIST = ROOT / "manifests/upstream/denominator-review-worklist.json"
        CANDIDATES = ROOT / "manifests/upstream/candidates"
        DEFAULT_OUTPUT = ROOT / "docs/review/denominator-family-packets"
        ID_KEYS = ("leaf_id", "stable_id", "id", "path", "symbol", "name", "key")
        COLLECTION_KEYS = (
            "leaves", "entries", "items", "operations", "methods", "symbols",
            "members", "fields", "records", "surface", "endpoints", "routes",
        )

        class PacketError(RuntimeError):
            pass

        def require(value: bool, message: str) -> None:
            if not value:
                raise PacketError(message)

        def canonical(value: Any) -> bytes:
            return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()

        def digest_bytes(value: bytes) -> str:
            return hashlib.sha256(value).hexdigest()

        def digest_file(path: Path) -> str:
            return digest_bytes(path.read_bytes())

        def scalar_by_key(value: Any, keys: set[str]) -> int | None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in keys and isinstance(item, int) and not isinstance(item, bool):
                        return item
                for item in value.values():
                    found = scalar_by_key(item, keys)
                    if found is not None:
                        return found
            elif isinstance(value, list):
                for item in value:
                    found = scalar_by_key(item, keys)
                    if found is not None:
                        return found
            return None

        def manifest_strings(value: Any) -> Iterable[str]:
            if isinstance(value, str):
                if value.endswith(".json") and "manifests/upstream/candidates" in value:
                    yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from manifest_strings(item)
            elif isinstance(value, list):
                for item in value:
                    yield from manifest_strings(item)

        def discover_manifests() -> list[Path]:
            paths: list[Path] = []
            if WORKLIST.is_file():
                worklist = json.loads(WORKLIST.read_text(encoding="utf-8"))
                for raw in manifest_strings(worklist):
                    path = ROOT / raw
                    if path.is_file() and path not in paths:
                        paths.append(path)
            if CANDIDATES.is_dir():
                for path in sorted(CANDIDATES.rglob("*.json")):
                    if path not in paths:
                        paths.append(path)
            filtered = []
            for path in paths:
                value = json.loads(path.read_text(encoding="utf-8"))
                if choose_leaf_collection(value, allow_empty=True) is not None:
                    filtered.append(path)
            return filtered

        def walk_lists(value: Any, pointer: str = "") -> Iterable[tuple[int, int, str, list[Any]]]:
            if isinstance(value, dict):
                for key, item in value.items():
                    escaped = key.replace("~", "~0").replace("/", "~1")
                    child = f"{pointer}/{escaped}"
                    if isinstance(item, list) and item:
                        recognized = 1 if key.lower() in COLLECTION_KEYS else 0
                        structured = sum(isinstance(row, dict) for row in item)
                        yield (recognized, structured, child, item)
                    yield from walk_lists(item, child)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    yield from walk_lists(item, f"{pointer}/{index}")

        def choose_leaf_collection(value: Any, allow_empty: bool = False) -> tuple[str, list[Any]] | None:
            candidates = list(walk_lists(value))
            if not candidates:
                if allow_empty:
                    return None
                raise PacketError("manifest has no non-empty candidate collection")
            # Prefer explicitly named leaf collections, then structured rows, then size.
            recognized = [row for row in candidates if row[0] == 1]
            pool = recognized or candidates
            pool.sort(key=lambda row: (row[1], len(row[3]), -row[2].count("/")), reverse=True)
            _, _, pointer, rows = pool[0]
            require(all(isinstance(row, (dict, str, int)) for row in rows), f"unsupported leaf collection at {pointer}")
            return pointer, rows

        def family_id(path: Path, value: Any) -> str:
            if isinstance(value, dict):
                for key in ("family_id", "family", "denominator_id", "id", "name"):
                    item = value.get(key)
                    if isinstance(item, str) and item.strip():
                        candidate = re.sub(r"[^A-Za-z0-9_.-]+", "-", item.strip()).strip("-")
                        if candidate:
                            return candidate
            return re.sub(r"[^A-Za-z0-9_.-]+", "-", path.stem).strip("-")

        def leaf_id(row: Any, index: int) -> str:
            if isinstance(row, dict):
                for key in ID_KEYS:
                    item = row.get(key)
                    if isinstance(item, (str, int)) and str(item).strip():
                        return str(item)
            if isinstance(row, (str, int)):
                return str(row)
            return f"sha256:{digest_bytes(canonical(row))}"

        def packet_for(path: Path) -> dict[str, Any]:
            value = json.loads(path.read_text(encoding="utf-8"))
            selected = choose_leaf_collection(value)
            assert selected is not None
            pointer, rows = selected
            seen: dict[str, int] = {}
            leaves = []
            for index, row in enumerate(rows):
                raw_id = leaf_id(row, index)
                occurrence = seen.get(raw_id, 0)
                seen[raw_id] = occurrence + 1
                stable_id = raw_id if occurrence == 0 else f"{raw_id}#duplicate-{occurrence}"
                leaves.append({
                    "leaf_id": stable_id,
                    "source_index": index,
                    "source_leaf_sha256": digest_bytes(canonical(row)),
                    "classification": None,
                    "rationale": None,
                    "evidence_ids": [],
                })
            require(leaves, f"{path}: empty leaf set")
            relative = path.relative_to(ROOT).as_posix()
            return {
                "schema": "trillionnium.denominator-family-review-packet.v1",
                "family_id": family_id(path, value),
                "source_manifest": relative,
                "source_manifest_sha256": digest_file(path),
                "source_leaf_pointer": pointer,
                "leaf_count": len(leaves),
                "leaves": leaves,
                "review_binding": {
                    "candidate_repository": "TrillionniumFoundation/TrillionniumGame",
                    "source_head": None,
                    "source_tree": None,
                    "prospective_merge": None,
                    "prospective_merge_tree": None,
                    "reviewer_login": None,
                    "reviewer_role": None,
                    "conflict_free_attestation": None,
                    "decision": None,
                },
                "claim_boundary": {
                    "family_classified": False,
                    "family_accepted": False,
                    "global_sg1_accepted": False,
                    "complete_nakama_compatibility": False,
                    "production_ready": False,
                },
            }

        def generate(output: Path) -> dict[str, Any]:
            parity = json.loads(PARITY.read_text(encoding="utf-8"))
            expected_families = scalar_by_key(parity, {"family_count", "denominator_family_count"}) or 14
            expected_leaves = scalar_by_key(parity, {"candidate_leaf_count", "leaf_count", "total_leaf_count"}) or 10173
            manifests = discover_manifests()
            require(len(manifests) == expected_families, f"expected {expected_families} candidate manifests, found {len(manifests)}")
            packets = [packet_for(path) for path in manifests]
            ids = [packet["family_id"] for packet in packets]
            require(len(ids) == len(set(ids)), "duplicate family IDs")
            packets.sort(key=lambda packet: packet["family_id"])
            total = sum(packet["leaf_count"] for packet in packets)
            require(total == expected_leaves, f"expected {expected_leaves} leaves, extracted {total}")
            if output.exists():
                shutil.rmtree(output)
            output.mkdir(parents=True)
            rows = []
            for packet in packets:
                path = output / f"{packet['family_id']}.candidate.json"
                path.write_bytes(canonical(packet))
                rows.append({
                    "family_id": packet["family_id"],
                    "packet": path.name,
                    "packet_sha256": digest_file(path),
                    "source_manifest": packet["source_manifest"],
                    "source_manifest_sha256": packet["source_manifest_sha256"],
                    "leaf_count": packet["leaf_count"],
                })
            index = {
                "schema": "trillionnium.denominator-family-review-index.v1",
                "parity_authority": PARITY.relative_to(ROOT).as_posix(),
                "parity_authority_sha256": digest_file(PARITY),
                "review_worklist": WORKLIST.relative_to(ROOT).as_posix(),
                "review_worklist_sha256": digest_file(WORKLIST),
                "family_count": len(rows),
                "leaf_count": total,
                "families": rows,
                "global_sg1_decision": None,
                "claim_boundary": {
                    "all_families_classified": False,
                    "all_families_accepted": False,
                    "global_sg1_accepted": False,
                    "complete_nakama_compatibility": False,
                    "production_ready": False,
                },
            }
            (output / "index.json").write_bytes(canonical(index))
            return index

        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
            args = parser.parse_args()
            report = generate(args.output.resolve())
            print(json.dumps({"families": report["family_count"], "leaves": report["leaf_count"]}, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def checker_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Reproduce and verify the complete denominator review packet family."""
        from __future__ import annotations

        import importlib.util
        import json
        import sys
        import tempfile
        from pathlib import Path

        ROOT = Path(__file__).resolve().parents[1]
        GENERATOR = ROOT / "scripts/generate-denominator-review-packets.py"
        PACKETS = ROOT / "docs/review/denominator-family-packets"

        class ValidationError(RuntimeError):
            pass

        def require(value: bool, message: str) -> None:
            if not value:
                raise ValidationError(message)

        def load_generator():
            spec = importlib.util.spec_from_file_location("denominator_packet_generator", GENERATOR)
            require(spec is not None and spec.loader is not None, "generator loader unavailable")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        def validate() -> dict:
            generator = load_generator()
            with tempfile.TemporaryDirectory() as temporary:
                generated = Path(temporary) / "packets"
                expected_index = generator.generate(generated)
                tracked = sorted(path.relative_to(PACKETS) for path in PACKETS.rglob("*") if path.is_file())
                reproduced = sorted(path.relative_to(generated) for path in generated.rglob("*") if path.is_file())
                require(tracked == reproduced, "packet file inventory drift")
                for relative in tracked:
                    require((PACKETS / relative).read_bytes() == (generated / relative).read_bytes(), f"packet drift: {relative}")
            index = json.loads((PACKETS / "index.json").read_text(encoding="utf-8"))
            require(index == expected_index, "index differs from reproduced authority")
            require(index.get("family_count") == 14, "family count")
            require(index.get("leaf_count") == 10173, "leaf count")
            for row in index.get("families", []):
                packet = json.loads((PACKETS / row["packet"]).read_text(encoding="utf-8"))
                require(packet.get("leaf_count") == len(packet.get("leaves", [])), f"{row['family_id']}: leaf count")
                require(all(leaf.get("classification") is None for leaf in packet["leaves"]), f"{row['family_id']}: synthesized classification")
                require(not any(packet.get("claim_boundary", {}).values()), f"{row['family_id']}: positive claim")
            require(not any(index.get("claim_boundary", {}).values()), "positive global claim")
            return index

        def main() -> int:
            try:
                index = validate()
            except (OSError, json.JSONDecodeError, ValidationError) as error:
                print(f"denominator review packet validation failed: {error}", file=sys.stderr)
                return 1
            print(json.dumps({"families": index["family_count"], "leaves": index["leaf_count"], "status": "reproducible-unclassified"}, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def family_accept_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Bind a human-supplied leaf-complete family decision without self-approval."""
        from __future__ import annotations

        import argparse
        import hashlib
        import json
        from pathlib import Path
        from typing import Any

        ALLOWED = {
            "compatible",
            "implemented-compatible",
            "intentional-divergence",
            "restricted-material-blocker",
            "not-applicable-with-rationale",
            "unimplemented-blocker",
        }

        class DecisionError(RuntimeError):
            pass

        def require(value: bool, message: str) -> None:
            if not value:
                raise DecisionError(message)

        def canonical(value: Any) -> bytes:
            return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()

        def sha(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def finalize(packet_path: Path, decision_path: Path, output: Path) -> dict[str, Any]:
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            require(packet.get("schema") == "trillionnium.denominator-family-review-packet.v1", "packet schema")
            require(decision.get("schema") == "trillionnium.denominator-family-human-decision.v1", "decision schema")
            require(decision.get("family_id") == packet.get("family_id"), "family identity")
            reviewer = decision.get("reviewer", {})
            require(isinstance(reviewer.get("login"), str) and reviewer["login"], "reviewer login")
            require(isinstance(reviewer.get("role"), str) and reviewer["role"], "reviewer role")
            require(reviewer.get("conflict_free_attestation") is True, "conflict-free attestation")
            require(reviewer.get("login") != decision.get("candidate_author"), "candidate author cannot self-approve")
            binding = decision.get("candidate_binding", {})
            for key in ("repository", "source_head", "source_tree", "prospective_merge", "prospective_merge_tree"):
                require(isinstance(binding.get(key), str) and binding[key], f"candidate binding {key}")
            require(binding["repository"] == "TrillionniumFoundation/TrillionniumGame", "repository binding")
            supplied = decision.get("leaves")
            require(isinstance(supplied, list), "leaf decisions")
            by_id = {row.get("leaf_id"): row for row in supplied if isinstance(row, dict)}
            require(len(by_id) == len(supplied), "duplicate or invalid leaf decisions")
            expected_ids = {row["leaf_id"] for row in packet["leaves"]}
            require(set(by_id) == expected_ids, "leaf denominator changed or incomplete")
            blockers = 0
            accepted_rows = []
            for leaf in packet["leaves"]:
                row = by_id[leaf["leaf_id"]]
                require(row.get("source_leaf_sha256") == leaf["source_leaf_sha256"], f"{leaf['leaf_id']}: source hash")
                classification = row.get("classification")
                require(classification in ALLOWED, f"{leaf['leaf_id']}: classification")
                rationale = row.get("rationale")
                require(isinstance(rationale, str) and rationale.strip(), f"{leaf['leaf_id']}: rationale")
                evidence = row.get("evidence_ids")
                require(isinstance(evidence, list), f"{leaf['leaf_id']}: evidence IDs")
                if classification in {"restricted-material-blocker", "unimplemented-blocker"}:
                    blockers += 1
                accepted_rows.append({
                    "leaf_id": leaf["leaf_id"],
                    "source_leaf_sha256": leaf["source_leaf_sha256"],
                    "classification": classification,
                    "rationale": rationale.strip(),
                    "evidence_ids": evidence,
                })
            require(decision.get("family_decision") in {"accept", "reject"}, "family decision")
            accepted = decision["family_decision"] == "accept" and blockers == 0
            result = {
                "schema": "trillionnium.denominator-family-decision.v1",
                "family_id": packet["family_id"],
                "packet_sha256": sha(packet_path),
                "source_manifest": packet["source_manifest"],
                "source_manifest_sha256": packet["source_manifest_sha256"],
                "candidate_binding": binding,
                "reviewer": reviewer,
                "decision_source_sha256": sha(decision_path),
                "leaf_count": len(accepted_rows),
                "blocker_count": blockers,
                "family_decision": decision["family_decision"],
                "accepted": accepted,
                "leaves": accepted_rows,
                "claim_boundary": {
                    "global_sg1_accepted": False,
                    "complete_nakama_compatibility": False,
                    "production_ready": False,
                },
            }
            output.write_bytes(canonical(result))
            return result

        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--packet", type=Path, required=True)
            parser.add_argument("--decision", type=Path, required=True)
            parser.add_argument("--output", type=Path, required=True)
            args = parser.parse_args()
            report = finalize(args.packet, args.decision, args.output)
            print(json.dumps({"family": report["family_id"], "accepted": report["accepted"], "blockers": report["blocker_count"]}, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def global_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Require 14 exact, accepted family decisions before a separate global SG1 decision."""
        from __future__ import annotations

        import argparse
        import hashlib
        import json
        from pathlib import Path

        class GlobalDecisionError(RuntimeError):
            pass

        def require(value: bool, message: str) -> None:
            if not value:
                raise GlobalDecisionError(message)

        def sha(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def finalize(index_path: Path, decisions: list[Path], global_input: Path, output: Path) -> dict:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            global_decision = json.loads(global_input.read_text(encoding="utf-8"))
            require(index.get("family_count") == 14 and index.get("leaf_count") == 10173, "denominator authority")
            rows = [json.loads(path.read_text(encoding="utf-8")) for path in decisions]
            by_family = {row.get("family_id"): (path, row) for path, row in zip(decisions, rows)}
            expected = {row["family_id"] for row in index["families"]}
            require(set(by_family) == expected, "family decision set incomplete or changed")
            binding = None
            family_records = []
            reviewers = set()
            for family in sorted(expected):
                path, row = by_family[family]
                require(row.get("schema") == "trillionnium.denominator-family-decision.v1", f"{family}: schema")
                require(row.get("accepted") is True, f"{family}: not accepted")
                require(row.get("blocker_count") == 0, f"{family}: blockers remain")
                current = row.get("candidate_binding")
                require(binding is None or current == binding, f"{family}: candidate identity drift")
                binding = current
                login = row.get("reviewer", {}).get("login")
                require(isinstance(login, str) and login, f"{family}: reviewer")
                reviewers.add(login)
                family_records.append({"family_id": family, "decision_sha256": sha(path), "reviewer_login": login})
            reviewer = global_decision.get("reviewer", {})
            require(reviewer.get("conflict_free_attestation") is True, "global reviewer conflict attestation")
            require(isinstance(reviewer.get("login"), str) and reviewer["login"], "global reviewer login")
            require(reviewer["login"] not in reviewers, "global SG1 reviewer must be distinct from family reviewers")
            require(global_decision.get("candidate_binding") == binding, "global candidate binding")
            require(global_decision.get("decision") in {"accept", "reject"}, "global decision")
            accepted = global_decision["decision"] == "accept"
            result = {
                "schema": "trillionnium.global-sg1-decision.v1",
                "index_sha256": sha(index_path),
                "candidate_binding": binding,
                "family_count": 14,
                "leaf_count": 10173,
                "families": family_records,
                "global_reviewer": reviewer,
                "global_input_sha256": sha(global_input),
                "decision": global_decision["decision"],
                "global_sg1_accepted": accepted,
                "claim_boundary": {
                    "complete_nakama_compatibility": False,
                    "production_ready": False,
                    "cutover_authorized": False,
                    "nakama_retired": False,
                },
            }
            output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return result

        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--index", type=Path, required=True)
            parser.add_argument("--global-decision", type=Path, required=True)
            parser.add_argument("--output", type=Path, required=True)
            parser.add_argument("decisions", nargs="+", type=Path)
            args = parser.parse_args()
            result = finalize(args.index, args.decisions, args.global_decision, args.output)
            print(json.dumps({"global_sg1_accepted": result["global_sg1_accepted"], "families": result["family_count"]}, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def test_source() -> str:
    return textwrap.dedent(
        '''\
        from __future__ import annotations

        import importlib.util
        import json
        import sys
        import tempfile
        from pathlib import Path
        import unittest

        ROOT = Path(__file__).resolve().parents[2]
        GENERATOR = ROOT / "scripts/generate-denominator-review-packets.py"
        CHECKER = ROOT / "scripts/check-denominator-review-packets.py"
        ACCEPT = ROOT / "scripts/accept-denominator-family.py"

        def load(path: Path, name: str):
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise RuntimeError("module unavailable")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module

        class DenominatorReviewPacketTests(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                cls.generator = load(GENERATOR, "denominator_generator_tests")
                cls.checker = load(CHECKER, "denominator_checker_tests")
                cls.accept = load(ACCEPT, "denominator_accept_tests")

            def test_tracked_packets_are_reproducible_and_complete(self):
                index = self.checker.validate()
                self.assertEqual(index["family_count"], 14)
                self.assertEqual(index["leaf_count"], 10173)

            def test_missing_leaf_decision_is_rejected(self):
                index = json.loads((ROOT / "docs/review/denominator-family-packets/index.json").read_text())
                packet_path = ROOT / "docs/review/denominator-family-packets" / index["families"][0]["packet"]
                packet = json.loads(packet_path.read_text())
                decision = {
                    "schema": "trillionnium.denominator-family-human-decision.v1",
                    "family_id": packet["family_id"],
                    "candidate_author": "author",
                    "candidate_binding": {"repository":"TrillionniumFoundation/TrillionniumGame","source_head":"a","source_tree":"b","prospective_merge":"c","prospective_merge_tree":"d"},
                    "reviewer": {"login":"reviewer","role":"compatibility","conflict_free_attestation":True},
                    "family_decision":"accept",
                    "leaves": [],
                }
                with tempfile.TemporaryDirectory() as temporary:
                    source = Path(temporary) / "decision.json"
                    output = Path(temporary) / "accepted.json"
                    source.write_text(json.dumps(decision))
                    with self.assertRaisesRegex(self.accept.DecisionError, "leaf denominator"):
                        self.accept.finalize(packet_path, source, output)

            def test_self_approval_is_rejected_before_leaf_processing(self):
                index = json.loads((ROOT / "docs/review/denominator-family-packets/index.json").read_text())
                packet_path = ROOT / "docs/review/denominator-family-packets" / index["families"][0]["packet"]
                packet = json.loads(packet_path.read_text())
                decision = {
                    "schema":"trillionnium.denominator-family-human-decision.v1",
                    "family_id":packet["family_id"],
                    "candidate_author":"same",
                    "candidate_binding":{"repository":"TrillionniumFoundation/TrillionniumGame","source_head":"a","source_tree":"b","prospective_merge":"c","prospective_merge_tree":"d"},
                    "reviewer":{"login":"same","role":"compatibility","conflict_free_attestation":True},
                    "family_decision":"reject",
                    "leaves":[],
                }
                with tempfile.TemporaryDirectory() as temporary:
                    source=Path(temporary)/"decision.json"; output=Path(temporary)/"accepted.json"; source.write_text(json.dumps(decision))
                    with self.assertRaisesRegex(self.accept.DecisionError,"self-approve"):
                        self.accept.finalize(packet_path,source,output)

        if __name__ == "__main__":
            unittest.main()
        '''
    )


def update_docs(root: Path) -> None:
    review = root / "docs/GOVERNANCE.md"
    text = read(review)
    section = textwrap.dedent(
        '''\

        ## Denominator family review packets

        `scripts/generate-denominator-review-packets.py` deterministically converts all fourteen pinned candidate manifests into 10,173 leaf-complete review packets. Every leaf binds its source hash and starts unclassified. `scripts/accept-denominator-family.py` rejects missing, duplicated, hash-changed or unrationalized decisions and rejects candidate-author self-approval. A family cannot be accepted while an unimplemented or restricted-material blocker remains.

        `scripts/finalize-global-sg1.py` requires all fourteen exact family decisions with one candidate identity and then a distinct, conflict-free global reviewer. Generating packets does not classify or accept any leaf; legal, compatibility and domain judgments remain human decisions.
        '''
    )
    if "## Denominator family review packets" not in text:
        text += section
    write(review, text)


def run(root: Path) -> None:
    require((root / ".git").is_dir(), "Git working tree required")
    write(root / "scripts/generate-denominator-review-packets.py", generator_source())
    write(root / "scripts/check-denominator-review-packets.py", checker_source())
    write(root / "scripts/accept-denominator-family.py", family_accept_source())
    write(root / "scripts/finalize-global-sg1.py", global_source())
    write(root / "tests/control_plane/test_denominator_review_packets.py", test_source())
    update_docs(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    run(parser.parse_args().root.resolve())
