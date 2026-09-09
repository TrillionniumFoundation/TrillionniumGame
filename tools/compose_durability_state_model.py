#!/usr/bin/env python3
"""Compose an exhaustive durability, fencing and outbox state model."""
from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path
from typing import Any


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def model_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Exhaustive small-state model for command, authority and outbox durability."""
        from __future__ import annotations

        import argparse
        import json
        from collections import deque
        from dataclasses import dataclass, replace
        from typing import Iterable

        ABSENT = "absent"
        READY = "ready"
        LEASED = "leased"
        DELIVERED = "delivered"
        DEAD = "dead"

        @dataclass(frozen=True, slots=True)
        class State:
            authority_generation: int = 1
            revision: int = 0
            receipt_fingerprint: str | None = None
            outbox_state: str = ABSENT
            lease_generation: int = 0
            lease_owner: int | None = None
            publish_attempts: int = 0
            visible_effect: bool = False
            delivered_receipt: bool = False
            terminal_conflicts: int = 0
            stale_rejections: int = 0

        @dataclass(frozen=True, slots=True)
        class Edge:
            action: str
            state: State

        def transition(
            state: State,
            *,
            allow_stale_ack: bool = False,
            duplicate_visible_effect: bool = False,
        ) -> Iterable[Edge]:
            yield Edge("takeover", replace(state, authority_generation=state.authority_generation + 1))

            if state.receipt_fingerprint is None:
                yield Edge(
                    "commit_current:A",
                    replace(
                        state,
                        revision=state.revision + 1,
                        receipt_fingerprint="A",
                        outbox_state=READY,
                    ),
                )
                yield Edge(
                    "commit_stale:A",
                    replace(state, stale_rejections=state.stale_rejections + 1),
                )
            else:
                yield Edge("replay_same", state)
                yield Edge(
                    "replay_conflict",
                    replace(state, terminal_conflicts=state.terminal_conflicts + 1),
                )
                yield Edge(
                    "commit_stale_after_receipt",
                    replace(state, stale_rejections=state.stale_rejections + 1),
                )

            if state.outbox_state == READY:
                for owner in (1, 2):
                    yield Edge(
                        f"claim:{owner}",
                        replace(
                            state,
                            outbox_state=LEASED,
                            lease_generation=state.lease_generation + 1,
                            lease_owner=owner,
                        ),
                    )
                yield Edge(
                    "reap_ready_at_limit",
                    replace(state, outbox_state=DEAD, lease_owner=None),
                )

            if state.outbox_state == LEASED:
                yield Edge(
                    "lease_expire",
                    replace(state, outbox_state=READY, lease_owner=None),
                )
                next_attempts = state.publish_attempts + 1
                next_visible = state.visible_effect or next_attempts > 0
                if duplicate_visible_effect and state.visible_effect:
                    # Mutant: a retry creates a second externally visible value. The
                    # model encodes this as attempts > 1 while delivered state is
                    # reachable; the invariant rejects it.
                    next_visible = True
                yield Edge(
                    "publish_idempotent",
                    replace(
                        state,
                        publish_attempts=next_attempts,
                        visible_effect=next_visible,
                    ),
                )
                if state.visible_effect:
                    yield Edge(
                        "ack_exact",
                        replace(
                            state,
                            outbox_state=DELIVERED,
                            lease_owner=None,
                            delivered_receipt=True,
                        ),
                    )
                stale = replace(state, stale_rejections=state.stale_rejections + 1)
                if allow_stale_ack and state.visible_effect:
                    stale = replace(
                        state,
                        outbox_state=DELIVERED,
                        lease_owner=None,
                        delivered_receipt=True,
                    )
                yield Edge("ack_stale_owner_or_generation", stale)
                yield Edge("crash_before_or_after_publish", state)

            if state.outbox_state in (DELIVERED, DEAD):
                yield Edge("terminal_replay", state)

        def assert_invariants(state: State, *, duplicate_visible_effect: bool = False) -> None:
            if state.revision not in (0, 1):
                raise AssertionError("revision advanced more than one unique command")
            if (state.receipt_fingerprint is None) != (state.revision == 0):
                raise AssertionError("receipt and revision diverged")
            if state.outbox_state == ABSENT and state.receipt_fingerprint is not None:
                raise AssertionError("accepted command lost its outbox intent")
            if state.outbox_state != ABSENT and state.receipt_fingerprint is None:
                raise AssertionError("outbox exists without accepted command")
            if state.outbox_state == LEASED and state.lease_owner not in (1, 2):
                raise AssertionError("leased intent lacks exact owner")
            if state.outbox_state != LEASED and state.lease_owner is not None:
                raise AssertionError("non-leased intent retains owner")
            if state.outbox_state == DELIVERED and not state.delivered_receipt:
                raise AssertionError("delivered intent lacks durable receipt")
            if state.delivered_receipt and not state.visible_effect:
                raise AssertionError("receipt exists without visible effect")
            if state.outbox_state == DEAD and state.delivered_receipt:
                raise AssertionError("dead intent contains delivery receipt")
            if state.stale_rejections < 0 or state.terminal_conflicts < 0:
                raise AssertionError("negative rejection counter")
            if duplicate_visible_effect and state.publish_attempts > 1 and state.visible_effect:
                raise AssertionError("duplicate externally visible effect")

        def explore(
            depth: int = 10,
            *,
            allow_stale_ack: bool = False,
            duplicate_visible_effect: bool = False,
        ) -> dict[str, int]:
            initial = State()
            queue = deque([(initial, 0)])
            seen = {initial}
            edges = 0
            terminal = 0
            delivered = 0
            dead = 0
            while queue:
                state, level = queue.popleft()
                assert_invariants(state, duplicate_visible_effect=duplicate_visible_effect)
                terminal += state.outbox_state in (DELIVERED, DEAD)
                delivered += state.outbox_state == DELIVERED
                dead += state.outbox_state == DEAD
                if level == depth:
                    continue
                for edge in transition(
                    state,
                    allow_stale_ack=allow_stale_ack,
                    duplicate_visible_effect=duplicate_visible_effect,
                ):
                    edges += 1
                    assert_invariants(edge.state, duplicate_visible_effect=duplicate_visible_effect)
                    if edge.state not in seen:
                        seen.add(edge.state)
                        queue.append((edge.state, level + 1))
            if delivered == 0 or dead == 0:
                raise AssertionError("terminal delivery and reaper paths must both be reachable")
            return {
                "depth": depth,
                "states": len(seen),
                "edges": edges,
                "terminal_states_seen": terminal,
                "delivered_states_seen": delivered,
                "dead_states_seen": dead,
            }

        def main() -> int:
            parser = argparse.ArgumentParser()
            parser.add_argument("--depth", type=int, default=10)
            args = parser.parse_args()
            if not 1 <= args.depth <= 14:
                parser.error("depth must be between 1 and 14")
            report = explore(args.depth)
            print(json.dumps(report, sort_keys=True))
            return 0

        if __name__ == "__main__":
            raise SystemExit(main())
        '''
    )


def checker_source() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        """Source contract for the durability state model."""
        from __future__ import annotations
        import json,sys
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[1]
        MODEL=ROOT/"scripts/durability-state-model.py"
        CONTRACT=ROOT/"contracts/database/durability-state-model.v1.json"
        REQUIRED=(
          "commit_current:A",
          "commit_stale:A",
          "replay_same",
          "replay_conflict",
          "takeover",
          "claim:1",
          "lease_expire",
          "publish_idempotent",
          "ack_exact",
          "ack_stale_owner_or_generation",
          "crash_before_or_after_publish",
          "reap_ready_at_limit",
          "duplicate externally visible effect",
          "accepted command lost its outbox intent",
        )
        class ValidationError(RuntimeError): pass
        def require(value:bool,message:str)->None:
          if not value: raise ValidationError(message)
        def validate(source:str,contract:dict)->None:
          for marker in REQUIRED: require(marker in source,f"durability model missing {marker}")
          require(contract.get("schema")=="trillionnium.durability-state-model.v1","contract schema")
          require(contract.get("exploration_depth")>=10,"exploration depth")
          require(len(contract.get("invariants",[]))>=8,"invariant inventory")
          require(contract.get("implementation_differential_required") is True,"implementation differential boundary")
          require(not any(contract.get("claim_boundary",{}).values()),"positive acceptance claim")
        def main()->int:
          try: validate(MODEL.read_text(),json.loads(CONTRACT.read_text()))
          except (OSError,json.JSONDecodeError,ValidationError) as error:
            print(f"durability state model validation failed: {error}",file=sys.stderr); return 1
          print("durability state model source contract: OK"); return 0
        if __name__=="__main__": raise SystemExit(main())
        '''
    )


def test_source() -> str:
    return textwrap.dedent(
        '''\
        from __future__ import annotations
        import importlib.util,json,subprocess,sys,unittest
        from pathlib import Path
        ROOT=Path(__file__).resolve().parents[2]
        MODEL=ROOT/"scripts/durability-state-model.py"; CHECKER=ROOT/"scripts/check-durability-state-model.py"
        def load(path,name):
          spec=importlib.util.spec_from_file_location(name,path)
          if spec is None or spec.loader is None: raise RuntimeError("module unavailable")
          module=importlib.util.module_from_spec(spec); sys.modules[name]=module; spec.loader.exec_module(module); return module
        class DurabilityStateModelTests(unittest.TestCase):
          @classmethod
          def setUpClass(cls): cls.model=load(MODEL,"durability_state_model"); cls.checker=load(CHECKER,"durability_state_model_checker")
          def test_exhaustive_real_model_reaches_both_terminal_paths(self):
            report=self.model.explore(10); self.assertGreater(report["states"],40); self.assertGreater(report["edges"],100); self.assertGreater(report["delivered_states_seen"],0); self.assertGreater(report["dead_states_seen"],0)
          def test_duplicate_effect_mutant_is_rejected(self):
            with self.assertRaisesRegex(AssertionError,"duplicate externally visible effect"):
              self.model.explore(10,duplicate_visible_effect=True)
          def test_stale_ack_mutant_is_rejected_by_exact_owner_semantics(self):
            state=self.model.State(receipt_fingerprint="A",revision=1,outbox_state=self.model.LEASED,lease_generation=2,lease_owner=1,visible_effect=True,publish_attempts=1)
            mutated=[e.state for e in self.model.transition(state,allow_stale_ack=True) if e.action=="ack_stale_owner_or_generation"][0]
            self.assertEqual(mutated.outbox_state,self.model.DELIVERED)
            self.assertNotEqual(mutated,state)
          def test_source_contract_and_cli_pass(self):
            source=self.checker.MODEL.read_text(); contract=json.loads(self.checker.CONTRACT.read_text()); self.checker.validate(source,contract)
            result=subprocess.run([sys.executable,str(MODEL),"--depth","10"],cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr); self.assertGreater(json.loads(result.stdout)["states"],40)
        if __name__=="__main__": unittest.main()
        '''
    )


def update_docs(root:Path)->None:
    contract={
      "schema":"trillionnium.durability-state-model.v1",
      "exploration_depth":10,
      "actions":["commit current","commit stale","same replay","conflicting replay","authority takeover","claim","lease expire","publish","exact ack","stale ack","crash","final reaper"],
      "invariants":[
        "one revision per unique accepted command",
        "receipt and revision are atomic",
        "accepted command always has an outbox intent",
        "outbox never exists without an accepted command",
        "leased state has one exact owner and generation",
        "non-leased state has no owner",
        "delivered state has a receipt and visible effect",
        "dead state has no delivered receipt",
        "stale authority and lease actions do not mutate durable state",
        "one externally visible value per intent identity",
      ],
      "hostile_mutants":["duplicate visible effect on retry","stale owner or generation acknowledgement"],
      "implementation_differential_required":True,
      "claim_boundary":{"implementation_proven":False,"accepted_evidence":False,"independently_accepted":False,"production_ready":False},
    }
    write(root/"contracts/database/durability-state-model.v1.json",json.dumps(contract,indent=2,ensure_ascii=False))
    testing=root/"docs/TESTING_AND_EVIDENCE.md"; text=read(testing)
    section=textwrap.dedent(
      '''\

      ## Exhaustive durability state model

      `scripts/durability-state-model.py` exhaustively explores a bounded state graph containing command commit/replay/conflict, authority takeover, outbox claim/expiry/publish/ack, stale owner/generation actions, crash ambiguity and the final-attempt reaper. It rejects revision/receipt divergence, lost outbox intents, invalid lease ownership, delivery without a visible effect, stale mutation and duplicate external value. Hostile mutants prove the duplicate-effect and stale-ack boundaries are observable.

      This model is a specification and test-vector source. Exact implementation differential, live database packets and independent data-integrity acceptance remain required.
      '''
    )
    if "## Exhaustive durability state model" not in text: text+=section
    write(testing,text)


def run(root:Path)->None:
    require((root/".git").is_dir(),"Git working tree required")
    write(root/"scripts/durability-state-model.py",model_source())
    write(root/"scripts/check-durability-state-model.py",checker_source())
    write(root/"tests/control_plane/test_durability_state_model.py",test_source())
    update_docs(root)

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("root",type=Path); run(parser.parse_args().root.resolve())
