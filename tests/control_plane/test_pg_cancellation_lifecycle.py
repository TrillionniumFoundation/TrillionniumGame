"""Structural regressions plus a finite interleaving model, not live DB evidence."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
import importlib.util
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
_TRIGGER_SPEC = importlib.util.spec_from_file_location(
    "trnm_pg_lifecycle_trigger_contract", ROOT / "scripts/workflow_trigger_contract.py"
)
if _TRIGGER_SPEC is None or _TRIGGER_SPEC.loader is None:
    raise RuntimeError("cannot load the lifecycle workflow trigger contract")
TRIGGER = importlib.util.module_from_spec(_TRIGGER_SPEC)
sys.modules[_TRIGGER_SPEC.name] = TRIGGER
_TRIGGER_SPEC.loader.exec_module(TRIGGER)
PARTS = Path("crates/trnm-persistence-pg/src/pool_parts")
WORKFLOW = Path(".github/workflows/pg-operation-deadline.yml")


def function(text: str, name: str) -> str:
    """Extract a named non-overloaded function from the reviewed source subset."""
    start = re.search(r"\bfn\s+" + re.escape(name) + r"\b", text)
    if start is None:
        return ""
    opening = text.find("{", start.end())
    depth, quoted, escaped, comment = 0, False, False, False
    for i in range(opening, len(text)):
        char = text[i]
        if comment:
            comment = char != "\n"
            continue
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if text[i:i + 2] == "//":
            comment = True
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return re.sub(r"//[^\n]*", "", text[opening + 1:i])
    return ""


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def validate(base: str, cancellation: str, pool: str, workflow: str) -> list[str]:
    """Tripwires complement, and do not replace, compiled Rust regression tests."""
    failures = []

    def need(condition: bool, label: str) -> None:
        if not condition:
            failures.append(label)

    register = compact(function(cancellation, "register"))
    publication = "inflight_operations.fetch_add(1,Ordering::Relaxed);drop(entries);"
    need(publication in register, "gauge publication must precede registry unlock")
    request = compact(function(cancellation, "request"))
    need("self.request_entry(&entry,reason)" in request, "request must use entry lifecycle")
    dispatch = compact(function(cancellation, "request_entry"))
    need("letretired=entry.retired.lock()" in dispatch, "entry-local dispatch mutex missing")
    need("if*retired{returnNone;}" in dispatch, "retired snapshots must not dispatch")
    need("compare_exchange(CANCEL_NONE,reason" in dispatch, "single delivery CAS missing")
    action = dispatch.find("(entry.action)()")
    release = dispatch.find("drop(retired);")
    need(0 <= action < release, "dispatch must hold the entry-local mutex")
    complete = compact(function(cancellation, "complete"))
    need(".remove(&id);ifletSome(entry)=entry{" in complete,
         "completion must release registry mutex before waiting")
    need("letmutretired=entry.retired.lock()" in complete, "completion mutex missing")
    need("*retired=true;self.metrics.inflight_operations.fetch_sub" in complete,
         "retirement must precede gauge decrement")
    manager = compact(base.split("pub(crate) enum ClientHandle", 1)[0])
    need("typePlainManager=RetirementManager<PostgresConnectionManager<NoTls>>;" in manager,
         "plaintext manager must evict retired leases")
    need("typeTlsManager=RetirementManager<PostgresConnectionManager<MakeTlsConnector>>;" in manager,
         "TLS manager must evict retired leases")
    broken = compact(function(base, "has_broken"))
    need("connection.retired.load(Ordering::Acquire)||self.inner.has_broken" in broken,
         "retired lease must be broken regardless of driver liveness")
    cancel = function(pool, "cancellation_action")
    for name, fragment in (("plaintext", "token.cancel_query(NoTls)"),
                           ("TLS", "token.cancel_query(connector.clone())")):
        end = cancel.find(fragment)
        begin = cancel.rfind("Arc::new(move ||", 0, end)
        need(0 <= begin < end and "retired.store(true, Ordering::Release);" in cancel[begin:end],
             name + " must retire before cancel transport I/O")
    run = compact(function(pool, "run_with_deadline"))
    need("repository.client.retirement_flag()" in run, "dispatch must share physical lease flag")
    need("ifcancellation_reason!=CANCEL_NONE||elapsed>=total_budget{repository.client.retire();}" in run,
         "late success and cancelled results must evict the lease")
    try:
        TRIGGER.validate_required_pr_and_main_paths(workflow, (
            "crates/trnm-persistence-pg/src/pool_parts/**",
            "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs",
            "tests/control_plane/test_pg_cancellation_lifecycle.py",
        ))
    except TRIGGER.TriggerContractError as error:
        failures.append(str(error))
    need("-p 'test_pg_cancellation_lifecycle.py' -v" in workflow,
         "workflow must execute the lifecycle regression suite")
    return failures


@dataclass(frozen=True)
class State:
    request: int = 0
    completion: int = 0
    present: bool = True
    captured: bool = False
    lock: str = ""
    retired: bool = False
    physical_retired: bool = False
    sent: bool = False
    delivered: bool = False
    returned: bool = False
    reused: bool = False
    bad: str = ""


def explore(guarded: bool, evict: bool) -> tuple[int, list[str]]:
    """Enumerate local callback/cleanup and delayed-wire-delivery interleavings.

    This is a finite abstraction of ordering, not a Rust memory-model proof or
    a statement about real network timing or successful backend cancellation.
    """
    initial = State()
    seen = {initial}
    work = deque([(initial, [])])
    counterexample: list[str] = []
    while work:
        s, trace = work.popleft()
        steps: list[tuple[str, State]] = []
        if s.request == 0:
            steps.append(("request snapshots entry", replace(
                s, request=1 if s.present else 4, captured=s.present)))
        elif s.request == 1 and (not guarded or not s.lock):
            if guarded and s.retired:
                steps.append(("retired snapshot rejected", replace(s, request=4)))
            else:
                steps.append(("request enters lifecycle", replace(
                    s, request=2, lock="request" if guarded else "")))
        elif s.request == 2:
            steps.append(("request dispatches cancel", replace(
                s, request=3, sent=True, physical_retired=evict,
                bad="dispatch after lease return" if s.returned else s.bad)))
        elif s.request == 3:
            steps.append(("request releases lifecycle", replace(s, request=4, lock="")))
        if s.completion == 0:
            steps.append(("completion removes registry entry", replace(s, completion=1, present=False)))
        elif s.completion == 1 and (not guarded or not s.lock):
            steps.append(("completion enters lifecycle", replace(
                s, completion=2, lock="completion" if guarded else "")))
        elif s.completion == 2:
            steps.append(("completion retires callback", replace(s, completion=3, retired=True)))
        elif s.completion == 3:
            steps.append(("completion releases lifecycle", replace(s, completion=4, lock="")))
        elif s.completion == 4:
            steps.append(("lease returned or evicted", replace(
                s, completion=5, returned=True, reused=not s.physical_retired)))
        if s.sent and not s.delivered:
            steps.append(("wire cancel arrives", replace(
                s, delivered=True, bad="wire cancel reaches reused backend" if s.reused else s.bad)))
        for label, next_state in steps:
            if next_state.bad and not counterexample:
                counterexample = trace + [label, next_state.bad]
            if next_state not in seen:
                seen.add(next_state)
                work.append((next_state, trace + [label]))
    return len(seen), counterexample


class LifecycleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sources = [
            (ROOT / PARTS / "base.rs").read_text(),
            (ROOT / PARTS / "cancellation.rs").read_text(),
            (ROOT / PARTS / "pool.rs").read_text(),
            (ROOT / WORKFLOW).read_text(),
        ]

    def test_current_source_contract(self) -> None:
        self.assertEqual(validate(*self.sources), [])

    def reject(self, index: int, before: str, after: str) -> None:
        self.assertEqual(validate(*self.sources), [], "hostile fixture requires a valid baseline")
        sources = self.sources.copy()
        self.assertIn(before, sources[index], "mutation must modify its intended source")
        sources[index] = sources[index].replace(before, after, 1)
        self.assertTrue(validate(*sources), "hostile mutation must fail")

    def test_reject_early_dispatch_unlock(self) -> None:
        self.reject(1, "(entry.action)()", "{ drop(retired); (entry.action)() }")

    def test_reject_retired_snapshot_bypass(self) -> None:
        self.reject(1, "if *retired {", "if false {")

    def test_reject_nonserialized_completion(self) -> None:
        self.reject(1, "*retired = true;", "*retired = false;")

    def test_reject_publication_after_unlock(self) -> None:
        self.reject(1, ".fetch_add(1, Ordering::Relaxed);\n        drop(entries);",
                    ".fetch_add(0, Ordering::Relaxed);\n        drop(entries);")

    def test_reject_recycling_retired_connection(self) -> None:
        self.reject(0, "|| self.inner.has_broken", "&& self.inner.has_broken")

    def test_reject_plaintext_dispatch_before_retirement(self) -> None:
        self.reject(2, "retired.store(true, Ordering::Release);", "retired.store(false, Ordering::Release);")

    def test_reject_missing_late_result_eviction(self) -> None:
        self.reject(2, "repository.client.retire();", "// no retirement")

    def test_reject_filtered_pull_request(self) -> None:
        self.reject(3, "  pull_request:\n",
                    "  pull_request:\n    paths: ['docs/**']\n")

    def test_reject_missing_push_path(self) -> None:
        sources = self.sources.copy()
        before = sources[3].index("  push:\n")
        sources[3] = sources[3][:before] + sources[3][before:].replace(
            "      - 'crates/trnm-persistence-pg/src/pool_parts/**'\n", "", 1)
        self.assertTrue(validate(*sources))

    def test_old_model_has_late_cancel_counterexample(self) -> None:
        self.assertTrue(explore(False, False)[1])

    def test_lock_only_does_not_fence_delayed_wire_cancel(self) -> None:
        self.assertTrue(explore(True, False)[1])

    def test_eviction_only_does_not_fence_stale_snapshot(self) -> None:
        self.assertTrue(explore(False, True)[1])

    def test_combined_fences_reject_all_modeled_interleavings(self) -> None:
        states, counterexample = explore(True, True)
        self.assertGreater(states, 20)
        self.assertEqual(counterexample, [])


if __name__ == "__main__":
    unittest.main()
