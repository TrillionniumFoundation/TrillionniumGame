# World command deployed runtime risk register v1

| Risk | Current control | Open evidence |
|---|---|---|
| External I/O under authority/storage lock | prepare/execute/verify/commit split | deployed pg activity/lock capture |
| Remote success with lost response | stable reservation/request identity | exact-head response-drop run |
| Partial core/World journal persistence | one multi-object StorageWrite batch; terminate on ambiguous ack | deployed atomicity/OCC evidence |
| Stale result overwrites newer authority state | version/sequence/state/tick/generation fences | deployed stale race matrix |
| Target failure falls back to legacy | explicit profile branch; no fallback source rule | independent Integration source review |
| Completion outcome is operator-invented | latest accepted World outcome binding | deployed completion run |
| Runtime restarts lose pending work | CAS journal and exact request replay | process-kill/restart evidence |
| Fixture evidence is mistaken for production | explicit source-candidate/NO-GO contracts | independent review and release governance |

Trillionnium Chain is excluded and no Chain risk is closed by these controls.
