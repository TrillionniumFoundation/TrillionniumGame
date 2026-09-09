#!/usr/bin/env python3
"""Compose the exact external-blocker handoff and attestation validators."""
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


def accept_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Validate one human or administrator external-fact attestation."""
        from __future__ import annotations

        import argparse
        import datetime as dt
        import hashlib
        import json
        from pathlib import Path
        from typing import Any

        class AttestationError(RuntimeError):
            pass

        def require(value: bool, message: str) -> None:
            if not value:
                raise AttestationError(message)

        def sha(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def parse_time(value: Any, label: str) -> dt.datetime:
            require(isinstance(value, str) and value, label)
            try:
                parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise AttestationError(label) from error
            require(parsed.tzinfo is not None, label)
            return parsed

        def validate_binding(binding: dict[str, Any]) -> None:
            require(binding.get("repository") == "TrillionniumFoundation/TrillionniumGame", "repository binding")
            for key in ("source_head", "source_tree", "prospective_merge", "prospective_merge_tree"):
                value = binding.get(key)
                require(isinstance(value, str) and len(value) == 40 and all(ch in "0123456789abcdef" for ch in value), f"candidate binding {key}")

        def accept(handoff: dict[str, Any], source_path: Path, output: Path, now: dt.datetime | None = None) -> dict[str, Any]:
            source = json.loads(source_path.read_text(encoding="utf-8"))
            require(source.get("schema") == "trillionnium.external-fact-attestation.v1", "attestation schema")
            blocker_id = source.get("blocker_id")
            by_id = {row["id"]: row for row in handoff.get("blockers", [])}
            require(blocker_id in by_id, "unknown blocker")
            blocker = by_id[blocker_id]
            require(source.get("actor_class") == blocker["actor_class"], "actor class")
            binding = source.get("candidate_binding")
            require(isinstance(binding, dict), "candidate binding")
            validate_binding(binding)
            actor = source.get("actor") or {}
            reviewer = source.get("reviewer") or {}
            require(isinstance(actor.get("login"), str) and actor["login"], "actor login")
            require(isinstance(reviewer.get("login"), str) and reviewer["login"], "reviewer login")
            require(isinstance(reviewer.get("role"), str) and reviewer["role"] in blocker["required_reviewer_roles"], "reviewer role")
            require(reviewer.get("conflict_free_attestation") is True, "conflict-free attestation")
            require(reviewer["login"] != actor["login"], "actor cannot accept own evidence")
            require(reviewer["login"] != source.get("candidate_author"), "candidate author cannot self-approve")
            require(reviewer["login"] not in set(source.get("evidence_producers") or []), "evidence producer cannot approve own evidence")
            evidence = source.get("evidence")
            require(isinstance(evidence, list) and evidence, "non-empty evidence")
            required_types = set(blocker["required_evidence_types"])
            observed_types = set()
            normalized = []
            for index, item in enumerate(evidence):
                require(isinstance(item, dict), f"evidence {index}")
                evidence_type = item.get("type")
                observed_types.add(evidence_type)
                identifier = item.get("id")
                digest = item.get("sha256")
                require(isinstance(identifier, str) and identifier, f"evidence {index} id")
                require(isinstance(digest, str) and len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest), f"evidence {index} digest")
                normalized.append({"type": evidence_type, "id": identifier, "sha256": digest})
            require(required_types <= observed_types, "required evidence types missing")
            decided_at = parse_time(source.get("decided_at"), "decided_at")
            expires_at = parse_time(source.get("expires_at"), "expires_at")
            now = now or dt.datetime.now(dt.timezone.utc)
            require(decided_at <= now < expires_at, "attestation expired or from the future")
            require(source.get("decision") == "accept", "decision is not accept")
            result = {
                "schema": "trillionnium.accepted-external-fact.v1",
                "blocker_id": blocker_id,
                "handoff_sha256": blocker.get("handoff_row_sha256"),
                "candidate_binding": binding,
                "actor": actor,
                "reviewer": reviewer,
                "evidence": normalized,
                "decided_at": source["decided_at"],
                "expires_at": source["expires_at"],
                "source_attestation_sha256": sha(source_path),
                "accepted": True,
                "claim_boundary": {
                    "all_external_facts_accepted": False,
                    "all_gaps_closed": False,
                    "production_ready": False,
                    "cutover_authorized": False,
                },
            }
            output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return result

        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--handoff", type=Path, required=True)
            parser.add_argument("--attestation", type=Path, required=True)
            parser.add_argument("--output", type=Path, required=True)
            args = parser.parse_args()
            result = accept(json.loads(args.handoff.read_text(encoding="utf-8")), args.attestation, args.output)
            print(json.dumps({"blocker_id": result["blocker_id"], "accepted": result["accepted"], "expires_at": result["expires_at"]}, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def finalize_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Require every exact external fact before emitting a bounded acceptance bundle."""
        from __future__ import annotations

        import argparse
        import datetime as dt
        import hashlib
        import json
        from pathlib import Path

        class FinalizationError(RuntimeError):
            pass

        def require(value: bool, message: str) -> None:
            if not value:
                raise FinalizationError(message)

        def sha(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def parse_time(value: str) -> dt.datetime:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))

        def finalize(handoff_path: Path, accepted_paths: list[Path], output: Path, now: dt.datetime | None = None) -> dict:
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            expected = {row["id"] for row in handoff.get("blockers", [])}
            rows = [json.loads(path.read_text(encoding="utf-8")) for path in accepted_paths]
            by_id = {row.get("blocker_id"): (path, row) for path, row in zip(accepted_paths, rows)}
            require(set(by_id) == expected, "external fact set incomplete or changed")
            binding = None
            now = now or dt.datetime.now(dt.timezone.utc)
            facts = []
            for blocker_id in sorted(expected):
                path, row = by_id[blocker_id]
                require(row.get("schema") == "trillionnium.accepted-external-fact.v1", f"{blocker_id}: schema")
                require(row.get("accepted") is True, f"{blocker_id}: not accepted")
                require(parse_time(row["decided_at"]) <= now < parse_time(row["expires_at"]), f"{blocker_id}: expired")
                current = row.get("candidate_binding")
                require(binding is None or current == binding, f"{blocker_id}: candidate drift")
                binding = current
                facts.append({"blocker_id": blocker_id, "accepted_fact_sha256": sha(path), "reviewer_login": row.get("reviewer", {}).get("login")})
            result = {
                "schema": "trillionnium.external-fact-acceptance-bundle.v1",
                "handoff_sha256": sha(handoff_path),
                "candidate_binding": binding,
                "fact_count": len(facts),
                "facts": facts,
                "all_external_facts_accepted": True,
                "claim_boundary": {
                    "repository_gap_register_closed": False,
                    "ordinary_protected_admission_complete": False,
                    "merged_main_evidence_accepted": False,
                    "production_ready": False,
                    "cutover_authorized": False,
                    "nakama_retired": False,
                },
            }
            output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return result

        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--handoff", type=Path, required=True)
            parser.add_argument("--output", type=Path, required=True)
            parser.add_argument("accepted", nargs="+", type=Path)
            args = parser.parse_args()
            result = finalize(args.handoff, args.accepted, args.output)
            print(json.dumps({"fact_count": result["fact_count"], "all_external_facts_accepted": result["all_external_facts_accepted"]}, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def checker_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Validate the exact external-blocker handoff contract."""
        from __future__ import annotations
        import hashlib,json,sys
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[1]
        HANDOFF=ROOT/"docs/status/PLAN_V32_EXTERNAL_BLOCKERS.json"
        ACCEPT=ROOT/"scripts/accept-external-fact.py"
        FINALIZE=ROOT/"scripts/finalize-external-fact-bundle.py"
        EXPECTED={"EXT-GITHUB-ADMIN","EXT-REVIEWER-CAPACITY","EXT-DENOMINATOR-ACCEPTANCE","EXT-KMS-HSM","EXT-ENDURANCE","EXT-RPO-RTO","EXT-COMPATIBILITY-IMPLEMENTATION","EXT-PROMOTION"}
        class ValidationError(RuntimeError): pass
        def require(value:bool,message:str)->None:
          if not value: raise ValidationError(message)
        def canonical(value): return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode()
        def validate(value:dict,accept:str,finalize:str)->None:
          require(value.get("schema")=="trillionnium.plan-v32-external-blocker-handoff.v1","schema")
          rows=value.get("blockers",[]); require({row.get("id") for row in rows}==EXPECTED,"blocker inventory")
          for row in rows:
            copied=dict(row); digest=copied.pop("handoff_row_sha256",None)
            require(digest==hashlib.sha256(canonical(copied)).hexdigest(),f"{row.get('id')}: row digest")
            require(row.get("actor_class"),f"{row.get('id')}: actor")
            require(row.get("required_reviewer_roles"),f"{row.get('id')}: reviewer roles")
            require(row.get("required_evidence_types"),f"{row.get('id')}: evidence types")
            require(row.get("issue_refs"),f"{row.get('id')}: issue refs")
            require(row.get("accepted") is False,f"{row.get('id')}: synthesized acceptance")
          for marker in ("actor cannot accept own evidence","candidate author cannot self-approve","evidence producer cannot approve own evidence","attestation expired or from the future","required evidence types missing"):
            require(marker in accept,f"acceptor missing {marker}")
          require("external fact set incomplete or changed" in finalize,"finalizer completeness")
          require(not any(value.get("claim_boundary",{}).values()),"positive handoff claim")
        def main()->int:
          try: validate(json.loads(HANDOFF.read_text()),ACCEPT.read_text(),FINALIZE.read_text())
          except (OSError,json.JSONDecodeError,ValidationError) as error:
            print(f"external blocker handoff validation failed: {error}",file=sys.stderr); return 1
          print("external blocker handoff source contract: OK"); return 0
        if __name__=="__main__": raise SystemExit(main())
        '''
    )


def test_source() -> str:
    return textwrap.dedent(
        '''\
        from __future__ import annotations
        import datetime as dt,importlib.util,json,sys,tempfile,unittest
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[2]
        ACCEPT=ROOT/"scripts/accept-external-fact.py"; CHECKER=ROOT/"scripts/check-external-blocker-handoff.py"
        def load(path,name):
          spec=importlib.util.spec_from_file_location(name,path)
          if spec is None or spec.loader is None: raise RuntimeError("module unavailable")
          module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module); return module
        class ExternalBlockerHandoffTests(unittest.TestCase):
          @classmethod
          def setUpClass(cls): cls.acceptor=load(ACCEPT,"external_accept_tests"); cls.checker=load(CHECKER,"external_checker_tests"); cls.handoff=json.loads(cls.checker.HANDOFF.read_text())
          def attestation(self,blocker):
            return {"schema":"trillionnium.external-fact-attestation.v1","blocker_id":blocker["id"],"actor_class":blocker["actor_class"],"candidate_author":"author","candidate_binding":{"repository":"TrillionniumFoundation/TrillionniumGame","source_head":"a"*40,"source_tree":"b"*40,"prospective_merge":"c"*40,"prospective_merge_tree":"d"*40},"actor":{"login":"actor"},"reviewer":{"login":"reviewer","role":blocker["required_reviewer_roles"][0],"conflict_free_attestation":True},"evidence_producers":["producer"],"evidence":[{"type":kind,"id":kind,"sha256":"e"*64} for kind in blocker["required_evidence_types"]],"decided_at":"2026-09-09T00:00:00Z","expires_at":"2026-10-09T00:00:00Z","decision":"accept"}
          def test_self_acceptance_and_missing_evidence_rejected(self):
            blocker=self.handoff["blockers"][0]
            value=self.attestation(blocker); value["reviewer"]["login"]="actor"
            with tempfile.TemporaryDirectory() as td:
              source=Path(td)/"input.json"; output=Path(td)/"out.json"; source.write_text(json.dumps(value))
              with self.assertRaisesRegex(self.acceptor.AttestationError,"own evidence"): self.acceptor.accept(self.handoff,source,output,dt.datetime(2026,9,10,tzinfo=dt.timezone.utc))
            value=self.attestation(blocker); value["evidence"]=[]
            with tempfile.TemporaryDirectory() as td:
              source=Path(td)/"input.json"; output=Path(td)/"out.json"; source.write_text(json.dumps(value))
              with self.assertRaisesRegex(self.acceptor.AttestationError,"non-empty evidence"): self.acceptor.accept(self.handoff,source,output,dt.datetime(2026,9,10,tzinfo=dt.timezone.utc))
          def test_valid_bounded_attestation_passes_without_global_promotion(self):
            blocker=self.handoff["blockers"][0]; value=self.attestation(blocker)
            with tempfile.TemporaryDirectory() as td:
              source=Path(td)/"input.json"; output=Path(td)/"out.json"; source.write_text(json.dumps(value))
              result=self.acceptor.accept(self.handoff,source,output,dt.datetime(2026,9,10,tzinfo=dt.timezone.utc))
              self.assertTrue(result["accepted"]); self.assertFalse(result["claim_boundary"]["all_gaps_closed"])
          def test_handoff_source_contract_passes(self): self.checker.validate(self.handoff,self.checker.ACCEPT.read_text(),self.checker.FINALIZE.read_text())
        if __name__=="__main__": unittest.main()
        '''
    )


def update_docs(root: Path) -> None:
    rows = [
        {"id":"EXT-GITHUB-ADMIN","actor_class":"repository-or-organization-administrator","required_reviewer_roles":["program-governance"],"required_evidence_types":["github-governance-readback","negative-no-bypass-rehearsal"],"issue_refs":["#7"],"required_fact":"Complete branch protection, ruleset, Actions and production-environment read-back with zero bypass and a harmless negative merge rehearsal."},
        {"id":"EXT-REVIEWER-CAPACITY","actor_class":"repository-owner-or-review-team-manager","required_reviewer_roles":["program-governance"],"required_evidence_types":["reviewer-roster","conflict-matrix"],"issue_refs":["#7","#15","#21","#35","#36","#46","#48"],"required_fact":"Redundant conflict-free qualified reviewer coverage for governance, security, cryptography, database, protocol, compatibility, legal, SRE and performance domains."},
        {"id":"EXT-DENOMINATOR-ACCEPTANCE","actor_class":"independent-compatibility-legal-and-domain-reviewers","required_reviewer_roles":["compatibility-architecture","legal","protocol","runtime","database-migration"],"required_evidence_types":["fourteen-family-decisions","global-sg1-decision"],"issue_refs":["#15","#21","#46"],"required_fact":"Fourteen exact leaf-complete family decisions and a distinct global SG1 decision bound to one candidate identity."},
        {"id":"EXT-KMS-HSM","actor_class":"security-platform-operator","required_reviewer_roles":["security","cryptography"],"required_evidence_types":["live-kms-hsm-run","iam-policy","audit-log","rotation-revoke-faults","performance"],"issue_refs":["#35"],"required_fact":"Approved vendor KMS/HSM or secret-manager MAC adapter with live IAM denial, audit, rotation, revoke, timeout, outage and tail-latency evidence."},
        {"id":"EXT-ENDURANCE","actor_class":"sre-performance-lab-owner","required_reviewer_roles":["sre","performance","database-migration"],"required_evidence_types":["capacity-threshold-decision","endurance-24h","endurance-72h","endurance-7d"],"issue_refs":["#36","#48"],"required_fact":"Accepted throughput/latency/resource thresholds and exact-candidate 24h, 72h and 7d hash-chained zero-failure endurance ledgers."},
        {"id":"EXT-RPO-RTO","actor_class":"service-owner-and-sre","required_reviewer_roles":["sre","database-migration","program-governance"],"required_evidence_types":["rpo-rto-decision","postgresql-failover","cockroachdb-failover","postgresql-pitr","rollback-rehearsal"],"issue_refs":["#36","#48"],"required_fact":"Approved RPO/RTO bound to accepted failover, PITR, restore and rollback packets for separate database profiles."},
        {"id":"EXT-COMPATIBILITY-IMPLEMENTATION","actor_class":"product-runtime-console-provider-sdk-owners","required_reviewer_roles":["compatibility-architecture","protocol","runtime","storage"],"required_evidence_types":["complete-nakama-denominator","oracle-differential","official-sdk-consumers"],"issue_refs":["#15","#21","#46"],"required_fact":"Complete Nakama API, RTAPI, Runtime, Console, provider/IAP and official SDK implementation with zero unexplained P0/P1 divergence."},
        {"id":"EXT-PROMOTION","actor_class":"independent-release-authority","required_reviewer_roles":["program-governance","sre","security"],"required_evidence_types":["ordinary-protected-admission","merged-main-packet","shadow-acceptance","exclusive-canary-acceptance","production-promotion","retirement-decision"],"issue_refs":["#7","#63"],"required_fact":"Ordinary protected admission, accepted merged-main packet, shadow and exclusive-canary decisions, production promotion and a separate explicit retirement decision."},
    ]
    import hashlib
    def canonical(value):
        return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode()
    for row in rows:
        row["accepted"] = False
        row["handoff_row_sha256"] = hashlib.sha256(canonical(row)).hexdigest()
    value={"schema":"trillionnium.plan-v32-external-blocker-handoff.v1","project_id":"trillionnium-game","plan_version":3,"blockers":rows,"acceptor":"python3 scripts/accept-external-fact.py","finalizer":"python3 scripts/finalize-external-fact-bundle.py","claim_boundary":{"all_external_facts_accepted":False,"all_gaps_closed":False,"accepted_evidence":False,"independent_acceptance":False,"complete_nakama_compatibility":False,"production_ready":False,"cutover_authorized":False,"nakama_retired":False}}
    write(root/"docs/status/PLAN_V32_EXTERNAL_BLOCKERS.json",json.dumps(value,indent=2,ensure_ascii=False))
    governance=root/"docs/GOVERNANCE.md"; text=read(governance)
    section=textwrap.dedent(
      '''\

      ## External blocker handoff

      `docs/status/PLAN_V32_EXTERNAL_BLOCKERS.json` is the exact handoff for the eight remaining classes of non-synthesizable fact. Each row names the responsible actor class, permitted reviewer roles, evidence types and issue routes. `scripts/accept-external-fact.py` requires exact candidate binding, non-empty digested evidence, bounded validity and a conflict-free reviewer who is neither actor, candidate author nor evidence producer. `scripts/finalize-external-fact-bundle.py` accepts only one current record for every blocker with one candidate identity.

      An accepted external-fact bundle still does not edit the gap register, merge protected main, approve production or retire Nakama. Those are subsequent ordinary protected and independently reviewed transitions.
      '''
    )
    if "## External blocker handoff" not in text: text+=section
    write(governance,text)


def run(root: Path) -> None:
    require((root/".git").is_dir(),"Git working tree required")
    write(root/"scripts/accept-external-fact.py",accept_source())
    write(root/"scripts/finalize-external-fact-bundle.py",finalize_source())
    write(root/"scripts/check-external-blocker-handoff.py",checker_source())
    write(root/"tests/control_plane/test_external_blocker_handoff.py",test_source())
    update_docs(root)

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("root",type=Path); run(parser.parse_args().root.resolve())
