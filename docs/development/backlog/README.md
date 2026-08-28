# Detailed execution backlog

`EXECUTION_BACKLOG.v2.json.gz` contains the complete audited 120-task backlog. It is compressed to keep repository review and API operations manageable while preserving canonical machine-readable JSON.

Inspect and validate it with:

```bash
python3 scripts/read-backlog.py --summary
python3 scripts/read-backlog.py --workstream W11
python3 scripts/read-backlog.py --task TG-W11-011
```

The authoritative SHA-256 and task count are recorded in `../EXECUTION_BACKLOG.json`. Do not edit the gzip by hand. Regenerate from a reviewed source, update the index digest, and run `python3 scripts/check-plan.py`.
