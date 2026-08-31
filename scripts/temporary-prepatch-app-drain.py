#!/usr/bin/env python3
"""Replace the worker-local App drain boolean with the process-shared fence."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match in {path}, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


app = ROOT / "crates/trnm-persistence-pg/src/bin/trnm_server/app.rs"
replace_once(
    app,
    "use std::sync::{Arc, Mutex};\n",
    "use std::sync::atomic::{AtomicBool, Ordering};\nuse std::sync::{Arc, Mutex};\n",
    "app atomic imports",
)
replace_once(app, "    draining: bool,\n", "    draining: Arc<AtomicBool>,\n", "app shared drain field")
replace_once(
    app,
    "    pub fn new(repository: R, admin_token: String) -> Self {\n"
    "        Self::with_shared_metrics(repository, admin_token, SharedAppMetrics::default())\n"
    "    }\n\n"
    "    #[must_use]\n"
    "    pub(crate) fn with_shared_metrics(\n"
    "        repository: R,\n"
    "        admin_token: String,\n"
    "        metrics: SharedAppMetrics,\n"
    "    ) -> Self {\n"
    "        Self {\n"
    "            repository,\n"
    "            admin_token,\n"
    "            sessions: SessionApi::default(),\n"
    "            draining: false,\n"
    "            metrics,\n"
    "        }\n"
    "    }\n",
    "    pub fn new(repository: R, admin_token: String) -> Self {\n"
    "        Self::with_shared_metrics(repository, admin_token, SharedAppMetrics::default())\n"
    "    }\n\n"
    "    #[must_use]\n"
    "    pub(crate) fn with_shared_metrics(\n"
    "        repository: R,\n"
    "        admin_token: String,\n"
    "        metrics: SharedAppMetrics,\n"
    "    ) -> Self {\n"
    "        Self::with_shared_state(\n"
    "            repository,\n"
    "            admin_token,\n"
    "            metrics,\n"
    "            Arc::new(AtomicBool::new(false)),\n"
    "        )\n"
    "    }\n\n"
    "    #[must_use]\n"
    "    pub(crate) fn with_shared_state(\n"
    "        repository: R,\n"
    "        admin_token: String,\n"
    "        metrics: SharedAppMetrics,\n"
    "        draining: Arc<AtomicBool>,\n"
    "    ) -> Self {\n"
    "        Self {\n"
    "            repository,\n"
    "            admin_token,\n"
    "            sessions: SessionApi::default(),\n"
    "            draining,\n"
    "            metrics,\n"
    "        }\n"
    "    }\n",
    "app shared-state constructors",
)
replace_once(
    app,
    "    pub const fn should_stop(&self) -> bool {\n        self.draining\n    }\n",
    "    pub fn should_stop(&self) -> bool {\n        self.draining.load(Ordering::Acquire)\n    }\n",
    "app should-stop fence",
)
text = app.read_text(encoding="utf-8")
if text.count("if self.draining {") != 3:
    raise SystemExit("app drain predicates changed unexpectedly")
text = text.replace("if self.draining {", "if self.draining.load(Ordering::Acquire) {")
if text.count("let ready = u8::from(!self.draining);") != 1:
    raise SystemExit("app readiness metric marker changed unexpectedly")
text = text.replace(
    "let ready = u8::from(!self.draining);",
    "let ready = u8::from(!self.draining.load(Ordering::Acquire));",
)
if text.count("self.draining = true;") != 1:
    raise SystemExit("app drain publication marker changed unexpectedly")
text = text.replace(
    "self.draining = true;",
    "self.draining.store(true, Ordering::Release);",
)
app.write_text(text, encoding="utf-8")

server = ROOT / "crates/trnm-persistence-pg/src/bin/trnm_server/server.rs"
replace_once(
    server,
    "    let mut app = App::with_shared_metrics(repository, config.admin_token.clone(), metrics);\n",
    "    let mut app = App::with_shared_state(\n"
    "        repository,\n"
    "        config.admin_token.clone(),\n"
    "        metrics,\n"
    "        Arc::clone(&draining),\n"
    "    );\n",
    "server app shared drain wiring",
)
print("process-shared App drain fence patched")
