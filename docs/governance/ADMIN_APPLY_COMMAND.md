# Administrator apply command

The following request is the intended GitHub API mutation, not proof of active state:

```http
POST /repos/TrillionniumFoundation/TrillionniumGame/rulesets
Content-Type: application/vnd.github+json

<docs/governance/RULESET_DESIRED_ACTIVE_REQUEST.json without request-only fields>
```

After application, read back `GET /repos/TrillionniumFoundation/TrillionniumGame/rulesets`, fetch the selected ruleset by ID, and verify its target, active enforcement, branch condition, bypass actors and every rule parameter. Until that readback is independently accepted, governance credit remains false.
