#!/usr/bin/env python3
"""Compose a fail-closed shadow/canary/production/retirement state machine."""
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


def machine_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Evaluate one cutover transition from accepted, exact evidence only."""
        from __future__ import annotations

        import argparse
        import hashlib
        import json
        from pathlib import Path
        from typing import Any

        TRANSITIONS = {
            "planning": "shadow",
            "shadow": "exclusive-canary",
            "exclusive-canary": "production",
            "production": "retirement-pending",
            "retirement-pending": "retired",
        }
        REQUIRED_GATES = {
            "shadow": {"SG0", "SG1", "SG2", "SG3", "SG4"},
            "exclusive-canary": {"SG0", "SG1", "SG2", "SG3", "SG4", "SG5"},
            "production": {"SG0", "SG1", "SG2", "SG3", "SG4", "SG5", "SG8"},
            "retirement-pending": {"SG0", "SG1", "SG2", "SG3", "SG4", "SG5", "SG8"},
            "retired": {"SG0", "SG1", "SG2", "SG3", "SG4", "SG5", "SG8"},
        }

        class TransitionError(RuntimeError):
            pass

        def require(value: bool, message: str) -> None:
            if not value:
                raise TransitionError(message)

        def sha(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def validate_binding(binding: dict[str, Any]) -> None:
            require(binding.get("repository") == "TrillionniumFoundation/TrillionniumGame", "repository binding")
            for key in ("source_head", "source_tree", "prospective_merge", "prospective_merge_tree"):
                value = binding.get(key)
                require(isinstance(value, str) and len(value) == 40 and all(ch in "0123456789abcdef" for ch in value), f"candidate binding {key}")

        def transition(current: dict[str, Any], request: dict[str, Any], blocker_packet: dict[str, Any]) -> dict[str, Any]:
            require(current.get("schema") == "trillionnium.cutover-state.v1", "current schema")
            require(request.get("schema") == "trillionnium.cutover-transition-request.v1", "request schema")
            source = current.get("state")
            target = request.get("target_state")
            require(TRANSITIONS.get(source) == target, "illegal or skipped transition")
            binding = request.get("candidate_binding")
            require(isinstance(binding, dict), "candidate binding")
            validate_binding(binding)
            existing_binding = current.get("candidate_binding")
            require(existing_binding in (None, binding), "candidate changed during promotion")
            require(blocker_packet.get("schema") == "trillionnium.cutover-blocker-packet.v1", "blocker packet schema")
            require(blocker_packet.get("candidate_binding") == binding, "blocker packet candidate mismatch")
            require(blocker_packet.get("open_p0_p1_count") == 0, "open P0/P1 blockers remain")
            require(blocker_packet.get("all_required_evidence_accepted") is True, "required evidence not accepted")
            require(blocker_packet.get("independent_review_complete") is True, "independent review incomplete")
            require(blocker_packet.get("governance_readback_complete") is True, "governance readback incomplete")

            accepted_gates = set(request.get("accepted_gates") or [])
            missing = REQUIRED_GATES[target] - accepted_gates
            require(not missing, "required gates missing: " + ",".join(sorted(missing)))
            reviewer = request.get("reviewer") or {}
            require(reviewer.get("conflict_free_attestation") is True, "reviewer conflict attestation")
            require(isinstance(reviewer.get("login"), str) and reviewer["login"], "reviewer login")
            require(reviewer["login"] != request.get("candidate_author"), "candidate author cannot approve transition")
            require(reviewer["login"] not in set(request.get("evidence_producers") or []), "evidence producer cannot approve own transition")
            rollback = request.get("rollback_packet") or {}
            require(rollback.get("accepted") is True, "accepted rollback packet required")
            require(isinstance(rollback.get("sha256"), str) and len(rollback["sha256"]) == 64, "rollback packet digest")
            require(request.get("ordinary_protected_admission") is True, "ordinary protected admission required")

            if target == "exclusive-canary":
                require(request.get("shadow_observation_accepted") is True, "shadow observation not accepted")
            if target == "production":
                require(request.get("exclusive_canary_accepted") is True, "exclusive canary not accepted")
                require(request.get("endurance_24h_accepted") is True, "24h endurance not accepted")
                require(request.get("endurance_72h_accepted") is True, "72h endurance not accepted")
                require(request.get("endurance_7d_accepted") is True, "7d endurance not accepted")
                require(request.get("approved_rpo_rto") is True, "RPO/RTO not approved")
            if target in {"retirement-pending", "retired"}:
                require(request.get("complete_nakama_compatibility") is True, "complete Nakama compatibility not accepted")
                require(request.get("global_sg1_accepted") is True, "global SG1 not accepted")
            if target == "retired":
                retirement = request.get("retirement_decision") or {}
                require(retirement.get("explicit") is True, "explicit retirement decision required")
                require(retirement.get("reviewer_login") != reviewer["login"], "retirement reviewer must be distinct")
                require(retirement.get("rollback_window_expired") is True, "rollback window not complete")

            history = list(current.get("history") or [])
            history.append({
                "from": source,
                "to": target,
                "request_sha256": request.get("request_sha256"),
                "reviewer_login": reviewer["login"],
                "accepted_gates": sorted(accepted_gates),
                "rollback_packet_sha256": rollback["sha256"],
            })
            return {
                "schema": "trillionnium.cutover-state.v1",
                "state": target,
                "candidate_binding": binding,
                "history": history,
                "claims": {
                    "public_online": target in {"production", "retirement-pending", "retired"},
                    "cutover_authorized": target in {"production", "retirement-pending", "retired"},
                    "nakama_retired": target == "retired",
                },
            }

        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--current", type=Path, required=True)
            parser.add_argument("--request", type=Path, required=True)
            parser.add_argument("--blockers", type=Path, required=True)
            parser.add_argument("--output", type=Path, required=True)
            args = parser.parse_args()
            current = json.loads(args.current.read_text(encoding="utf-8"))
            request = json.loads(args.request.read_text(encoding="utf-8"))
            request["request_sha256"] = sha(args.request)
            blockers = json.loads(args.blockers.read_text(encoding="utf-8"))
            result = transition(current, request, blockers)
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"from": current["state"], "to": result["state"], "claims": result["claims"]}, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def derive_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Derive the current cutover blocker packet without promoting any claim."""
        from __future__ import annotations

        import argparse
        import json
        from pathlib import Path

        CLOSED = {"closed", "rejected", "superseded"}

        def derive(gap_register: dict, binding: dict) -> dict:
            open_rows = [
                {"id": row.get("id"), "severity": row.get("severity"), "status": row.get("status"), "external_dependency": row.get("external_dependency")}
                for row in gap_register.get("gaps", [])
                if row.get("severity") in {"P0", "P1"} and row.get("status") not in CLOSED
            ]
            open_rows.sort(key=lambda row: (row.get("severity") or "", row.get("id") or ""))
            return {
                "schema": "trillionnium.cutover-blocker-packet.v1",
                "candidate_binding": binding,
                "open_p0_p1_count": len(open_rows),
                "open_p0_p1": open_rows,
                "all_required_evidence_accepted": False,
                "independent_review_complete": False,
                "governance_readback_complete": False,
                "claim_boundary": {
                    "shadow_authorized": False,
                    "exclusive_canary_authorized": False,
                    "production_authorized": False,
                    "retirement_authorized": False,
                },
            }

        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--gap-register", type=Path, required=True)
            parser.add_argument("--binding", type=Path, required=True)
            parser.add_argument("--output", type=Path, required=True)
            args = parser.parse_args()
            value = derive(json.loads(args.gap_register.read_text()), json.loads(args.binding.read_text()))
            args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
            print(json.dumps({"open_p0_p1_count": value["open_p0_p1_count"]}, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def checker_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Source contract for the cutover and retirement state machine."""
        from __future__ import annotations
        import json,sys
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[1]
        MACHINE=ROOT/"scripts/cutover-state-machine.py"
        DERIVE=ROOT/"scripts/derive-cutover-blocker-packet.py"
        CONTRACT=ROOT/"contracts/operations/cutover-state-machine.v1.json"
        REQUIRED=("planning","shadow","exclusive-canary","production","retirement-pending","retired","illegal or skipped transition","open P0/P1 blockers remain","required evidence not accepted","independent review incomplete","governance readback incomplete","candidate author cannot approve transition","evidence producer cannot approve own transition","accepted rollback packet required","ordinary protected admission required","24h endurance not accepted","72h endurance not accepted","7d endurance not accepted","RPO/RTO not approved","complete Nakama compatibility not accepted","global SG1 not accepted","explicit retirement decision required","retirement reviewer must be distinct")
        class ValidationError(RuntimeError): pass
        def require(value:bool,message:str)->None:
          if not value: raise ValidationError(message)
        def validate(machine:str,derive:str,contract:dict)->None:
          for marker in REQUIRED: require(marker in machine,f"cutover machine missing {marker}")
          require("open_p0_p1_count" in derive,"blocker derivation missing")
          require(contract.get("schema")=="trillionnium.cutover-state-machine.v1","contract schema")
          require(contract.get("states")==["planning","shadow","exclusive-canary","production","retirement-pending","retired"],"state order")
          require(contract.get("skip_transitions_allowed") is False,"skip boundary")
          require(contract.get("self_approval_allowed") is False,"self approval boundary")
          require(not any(contract.get("claim_boundary",{}).values()),"positive cutover claim")
        def main()->int:
          try: validate(MACHINE.read_text(),DERIVE.read_text(),json.loads(CONTRACT.read_text()))
          except (OSError,json.JSONDecodeError,ValidationError) as error:
            print(f"cutover state-machine validation failed: {error}",file=sys.stderr); return 1
          print("cutover state-machine source contract: OK"); return 0
        if __name__=="__main__": raise SystemExit(main())
        '''
    )


def test_source() -> str:
    return textwrap.dedent(
        '''\
        from __future__ import annotations
        import importlib.util,sys,unittest
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[2]
        MACHINE=ROOT/"scripts/cutover-state-machine.py"; DERIVE=ROOT/"scripts/derive-cutover-blocker-packet.py"
        def load(path,name):
          spec=importlib.util.spec_from_file_location(name,path)
          if spec is None or spec.loader is None: raise RuntimeError("module unavailable")
          module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module); return module
        class CutoverStateMachineTests(unittest.TestCase):
          @classmethod
          def setUpClass(cls): cls.machine=load(MACHINE,"cutover_machine_tests"); cls.derive=load(DERIVE,"cutover_derive_tests")
          def binding(self): return {"repository":"TrillionniumFoundation/TrillionniumGame","source_head":"a"*40,"source_tree":"b"*40,"prospective_merge":"c"*40,"prospective_merge_tree":"d"*40}
          def request(self,target="shadow"):
            return {"schema":"trillionnium.cutover-transition-request.v1","target_state":target,"candidate_binding":self.binding(),"accepted_gates":["SG0","SG1","SG2","SG3","SG4"],"reviewer":{"login":"reviewer","conflict_free_attestation":True},"candidate_author":"author","evidence_producers":["producer"],"rollback_packet":{"accepted":True,"sha256":"e"*64},"ordinary_protected_admission":True,"request_sha256":"f"*64}
          def clear_blockers(self): return {"schema":"trillionnium.cutover-blocker-packet.v1","candidate_binding":self.binding(),"open_p0_p1_count":0,"all_required_evidence_accepted":True,"independent_review_complete":True,"governance_readback_complete":True}
          def test_open_gap_and_self_approval_rejected(self):
            current={"schema":"trillionnium.cutover-state.v1","state":"planning","candidate_binding":None,"history":[]}
            blockers=self.clear_blockers(); blockers["open_p0_p1_count"]=1
            with self.assertRaisesRegex(self.machine.TransitionError,"open P0/P1"): self.machine.transition(current,self.request(),blockers)
            request=self.request(); request["reviewer"]["login"]="author"
            with self.assertRaisesRegex(self.machine.TransitionError,"self|candidate author"): self.machine.transition(current,request,self.clear_blockers())
          def test_skipped_transition_and_missing_rollback_rejected(self):
            current={"schema":"trillionnium.cutover-state.v1","state":"planning","candidate_binding":None,"history":[]}
            with self.assertRaisesRegex(self.machine.TransitionError,"illegal or skipped"): self.machine.transition(current,self.request("production"),self.clear_blockers())
            request=self.request(); request["rollback_packet"]["accepted"]=False
            with self.assertRaisesRegex(self.machine.TransitionError,"rollback"): self.machine.transition(current,request,self.clear_blockers())
          def test_valid_planning_to_shadow_only_sets_shadow(self):
            current={"schema":"trillionnium.cutover-state.v1","state":"planning","candidate_binding":None,"history":[]}
            result=self.machine.transition(current,self.request(),self.clear_blockers())
            self.assertEqual(result["state"],"shadow"); self.assertFalse(result["claims"]["public_online"])
          def test_real_gap_register_derives_blocked_packet(self):
            import json
            gaps=json.loads((ROOT/"docs/status/GAP_REGISTER.json").read_text())
            packet=self.derive.derive(gaps,self.binding())
            self.assertGreater(packet["open_p0_p1_count"],0); self.assertFalse(packet["all_required_evidence_accepted"])
        if __name__=="__main__": unittest.main()
        '''
    )


def update_docs(root: Path) -> None:
    contract={
      "schema":"trillionnium.cutover-state-machine.v1",
      "states":["planning","shadow","exclusive-canary","production","retirement-pending","retired"],
      "skip_transitions_allowed":False,
      "self_approval_allowed":False,
      "evidence_producer_approval_allowed":False,
      "rollback_packet_required_for_every_transition":True,
      "ordinary_protected_admission_required":True,
      "production_requires":["zero open P0/P1","all evidence accepted","independent review","governance readback","SG0 SG1 SG2 SG3 SG4 SG5 SG8","exclusive canary acceptance","24h 72h 7d endurance acceptance","approved RPO/RTO"],
      "retirement_requires":["complete Nakama compatibility","global SG1","explicit distinct retirement decision","rollback window expired"],
      "claim_boundary":{"shadow_authorized":False,"exclusive_canary_authorized":False,"production_authorized":False,"retirement_authorized":False,"nakama_retired":False},
    }
    write(root/"contracts/operations/cutover-state-machine.v1.json",json.dumps(contract,indent=2,ensure_ascii=False))
    initial={"schema":"trillionnium.cutover-state.v1","state":"planning","candidate_binding":None,"history":[],"claims":{"public_online":False,"cutover_authorized":False,"nakama_retired":False}}
    write(root/"docs/status/CUTOVER_STATE.json",json.dumps(initial,indent=2))
    operations=root/"docs/OPERATIONS_AND_RELEASE.md"; text=read(operations)
    section=textwrap.dedent(
      '''\

      ## Cutover and retirement state machine

      `scripts/cutover-state-machine.py` permits only the ordered sequence planning → shadow → exclusive canary → production → retirement pending → retired. Every edge binds the exact source/merge identity, zero open P0/P1 gaps, accepted evidence, independent review, complete governance read-back, ordinary protected admission and an accepted rollback packet. Production additionally requires accepted 24h/72h/7d endurance and approved RPO/RTO; retirement requires complete Nakama compatibility, global SG1 and a distinct explicit retirement reviewer.

      `scripts/derive-cutover-blocker-packet.py` derives the current blocked state directly from the gap register. Source code, CI success or administrator power cannot skip a state or manufacture an approval.
      '''
    )
    if "## Cutover and retirement state machine" not in text: text+=section
    write(operations,text)


def run(root: Path) -> None:
    require((root/".git").is_dir(),"Git working tree required")
    write(root/"scripts/cutover-state-machine.py",machine_source())
    write(root/"scripts/derive-cutover-blocker-packet.py",derive_source())
    write(root/"scripts/check-cutover-state-machine.py",checker_source())
    write(root/"tests/control_plane/test_cutover_state_machine.py",test_source())
    update_docs(root)

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("root",type=Path); run(parser.parse_args().root.resolve())
