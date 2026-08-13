# ADR 0003: WS8 Admission publishes ready identities to Range Core

- Status: Accepted
- Date: 2026-08-14
- Issue: #59

## Context

Admission creates a `player_id` when a seat enters `requested`, but a failed
provision must never become a Range Core identity. Range Core currently starts
an exercise and its Red roster in one request, then attributes gameplay from
the direct TCP peer address. WS8 also gives Blue players an identity for seat
ownership without introducing an individual Blue score.

Release must prevent the old player from receiving future attribution while
retaining completed-objective and hint records as historical evidence.

## Decision

Range Core exposes an additive service-to-service lifecycle:

- `POST /api/exercises/prepare` reserves an exercise identifier without making
  the exercise current.
- `PUT /api/exercises/{exercise_id}/players/{player_id}` publishes one ready
  player idempotently. The body is `{"team":"red","source_ip":"..."}` for
  Red and `{"team":"blue"}` for Blue.
- `GET` on the same player path returns only an active published identity.
- `DELETE` on the same player path idempotently revokes it.
- `POST /api/exercises/start` with `{"exercise_id":"..."}` starts a prepared
  exercise. The existing `scenario_id` plus `players` request remains valid for
  compatibility.

Prepared identities and Admission player records are additive tables. Active
Red players are projected into the existing exercise roster. Revocation marks
both records inactive instead of deleting them, so objective completions,
hints, and event evidence remain intact. Blue players are retained as seat
identities but are never projected into Red source-IP scoring.

The lifecycle endpoints accept only a bearer token resolving to the explicit
`admission` service role. Missing, unknown, player, and instructor tokens fail
closed on this seam. `RANGE_CORE_TOKEN_ADMISSION` uses the existing Range Core
service-token namespace but is not added to the disclosure-clearance hierarchy:
it is an API capability, not a gameplay/evidence visibility level. The role is
also rejected from scenario, gameplay, scoring, start, and reset endpoints.

## Trust-boundary consequence

This does not route gameplay through Z-EDGE and does not trust proxy identity
headers. Red registration associates the provisioned Kali address with the
player, but submissions and hints still resolve that association exclusively
from FastAPI/Starlette's TCP peer (`request.client.host`). `X-Forwarded-For`,
`X-Real-IP`, and caller-supplied identity fields grant no attribution.

Therefore Admission may publish lifecycle metadata over the authenticated
Z-APP service seam, while gameplay retains the existing direct Kali-to-Range
Core trust boundary. A reverse proxy or NAT in that path would still break
attribution closed with HTTP 403; this ADR does not authorize such a topology.

## Consequences

- A `requested` player is absent from Range Core until Admission observes
  `ready` and calls `PUT`.
- Retrying ready publication returns the original stable representation.
- Releasing a Red seat immediately removes future peer-IP attribution and
  permits that source address to be assigned to a new player; the old player's
  rows remain historical.
- Blue receives stable identity/seat ownership with no source-IP mapping and no
  individual score.
- Only one prepared exercise and one running exercise are allowed; preparing
  while an exercise is active fails with conflict rather than mutating it.
