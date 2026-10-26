# Review & Merge Orchestrator — Architecture Design

Oct 25, 2026 · @Emad

## Overview

The Review & Merge Orchestrator is one codebase, run as an API and a background worker, with PostgreSQL, deployed on-premise next to the self-managed GitLab. GitLab stays the source of truth for code, approvals and merges. The orchestrator owns bookings, budgets, ownership rules and pipeline state, and enforces the rules GitLab cannot express on its own.

The diagrams follow the C4 model: system context (level 1), containers (level 2) and the components of the core (level 3), plus a deployment view. Key flows are written as numbered dynamic views.

**In scope**

- Orchestrating stages 2 to 4: senior approval, review and merge, release approval.
- Booking reviewers by ownership, capacity and budget, including multi-area MRs and the coverage policy.
- Urgency approval, reviewer continuity, drop-out handover, waiting-on-author tracking.
- Merge routing with separation of duties, release candidates and generated release notes.
- Notifications, the web app, and process analytics.

**Out of scope**

- The AI Reviewer. Another team owns it; the orchestrator only reads its verdict from GitLab.
- Code hosting, CI execution and the deployment mechanics. The existing pipeline still deploys; the orchestrator records and gates the CTO's release approval.

**Constraints that shape the design**

- Every MR passes all four stages. No skips.
- The stage 2 approvers, the merge owner and the author are always different people.
- About 10 senior reviewers and 20 or more MRs a day. Volume is small, so correctness, auditability and simple operation matter more than scale.
- Time tracking stays minimal: slot outcomes only, no activity monitoring.

**Quality goals, in priority order**

1. Routing rules are always applied correctly, and every decision can be explained.
2. Nothing gets stuck: every MR has an owner or raises an alert.
3. Every state change is auditable.
4. Simple to run for a small team inside the company network.

## Level 1: System context

Six roles use one system. The orchestrator reads from and writes to GitLab, and reads calendar and directory data; it never talks to the AI Reviewer, which reports its verdict into GitLab.

&#91;embedded content: C4 level 1 · system context\]

Shaded boxes are systems outside this project; the dashed border marks the one owned by another team.

## Level 2: Containers

The system is four containers. The API takes everything coming in (people, GitLab webhooks, chat button callbacks); the worker does everything that goes out or runs on a timer. Both share one PostgreSQL database, which also serves as the job queue.

&#91;embedded content: C4 level 2 · containers\]

| Container | Technology | Responsibility |
| --- | --- | --- |
| Web app | Single-page app served as static files | Queues, booking calendar, budget settings, release approval, admin, dashboards |
| Orchestrator API | Modular monolith exposing REST | Webhook ingress into the inbox, user and chat actions, sign-in through GitLab, rule evaluation on request |
| Worker and scheduler | Same codebase, run as a background process | Processes inbox events, runs bookings and daily rollover, sends outbox calls, syncs calendar and directory, runs the watchdog and reconciliation |
| PostgreSQL | Relational database | All state, inbox and outbox tables, job queue, versioned rule settings, append-only audit log |

Chat button callbacks reach the API through the same ingress as webhooks; the arrow is left out to keep the drawing readable.

## Level 3: Components

The API and the worker share one codebase in three layers. Entry points turn requests and events into commands; domain modules hold every rule; only the adapters at the bottom talk to GitLab, chat, the calendar or the database. The booking engine is highlighted because it carries the most logic and the most risk.

&#91;embedded content: C4 level 3 · components of the API and worker\]

Domain modules depend on adapter interfaces, never on a vendor client, so each module can be tested with fakes and a different chat or calendar product changes one adapter. The main dependencies between domain modules:

| Component | Depends on | Triggered by |
| --- | --- | --- |
| MR lifecycle | Area resolver, Booking engine, Merge router, Release and notes | Inbox events, user actions |
| Area resolver | Ownership and coverage rules | MR lifecycle on AI Reviewer pass |
| Booking engine | Capacity and budget, Continuity and handover, Urgency workflow | New area review, follow-up, daily run |
| Continuity and handover | Booking engine, GitLab gateway (threads for the package) | Hand back, delegation, leave, watchdog |
| Urgency workflow | Team directory, Booking engine | Author's urgent flag |
| Author-wait tracker | Capacity and budget (releases reserve) | Changes requested |
| Merge router | Area review approvals, Capacity and budget | MR approved at stage 2 |
| Release and notes | Merged MRs, audit events | Merge, CTO action |

## Key flows (dynamic views)

Every flow starts from a GitLab event, a user action or a scheduled run, and ends in a state change written to the audit log. Each step names the component from the level 3 view that does it.

**1. MR intake**

1. GitLab sends a merge request webhook. Webhook ingress checks the secret token, stores the event in the inbox table keyed by its event id, and replies at once.
2. The worker takes events in order per MR. MR lifecycle creates or updates the MR record.
3. Readiness checks run: CI status, description present, size against the area's limit. An oversized MR gets a size warning to the author.
4. MR lifecycle waits for the AI Reviewer's verdict, read from GitLab. A pass moves the MR to Awaiting booking; a fail moves it to Waiting on author.
5. Area resolver maps the changed files to code areas and applies the coverage policy: one area review per area (strict), or one review by a code owner of all areas (combined).

**2. Booking**

1. Booking runs on each new area review, on each follow-up, and once per working day for everything that rolled over.
2. Continuity and handover returns the current reviewer if the area already has one.
3. Otherwise Booking engine filters candidates (owner of the area, not the author, not on leave, working that day), then scores them by owner tier, remaining capacity, queue length and recent load.
4. Capacity and budget answers how much unreserved time each candidate has per day; follow-ups may use the continuation reserve.
5. Booking engine places the slot on the earliest day that fits, ranks the day (urgent, follow-ups, oldest, size), and stores an explanation of why this reviewer and this day.
6. GitLab gateway sets the reviewer on the MR. Notification service sends the booking to the reviewer.

**3. Review session outcome**

1. When the slot ends, the reviewer answers in chat or the web app: done, need more time, or hand back.
2. Need more time books the next session with the same reviewer. Approval is read from GitLab when the reviewer approves there.
3. When every area is covered, MR lifecycle moves the MR to Approved and calls Merge router.

**4. Reviewer drop-out**

1. Triggers: leave found by the calendar sync, a hand back, a delegation, or Scheduler and watchdog finding missed slots past the threshold.
2. Continuity and handover releases the area review, keeps the MR's age and urgent flag, and builds the handover package from GitLab threads and session history.
3. Booking engine books a new reviewer with continuation priority. Approvals for other areas stay valid.
4. No eligible candidate: Notification service alerts the author's tech lead and the admin.

**5. Urgency**

1. The author flags the MR as urgent with a reason. Urgency workflow finds the approver through the reporting line (one level up if the author is a lead; a delegate if the lead is away).
2. On approval, Booking engine books the best available owner today, ignoring budget but not availability, and logs the overage.

**6. Waiting on author**

1. Changes requested at stage 2 or 3 start the author-wait clock and reminders.
2. After the threshold, Capacity and budget releases the reviewer's held reserve. The reviewer stays assigned.
3. A new commit restarts the AI Reviewer gate; the follow-up goes back to the same reviewer.

**7. Merge routing**

1. Merge router picks an eligible merge owner by area, availability and load, excluding the author and every stage 2 approver.
2. The merge owner reviews and merges. Separation of duties is enforced at merge time (see decision 6).

**8. Release**

1. Release and notes adds merged MRs to the open release candidate and generates release notes.
2. The CTO or delegate approves the candidate in the web app; the existing deploy pipeline runs and the MRs move to Deployed.

**9. Reconciliation**

1. Every few minutes, GitLab gateway lists MRs changed since the last run and compares them with stored state. Missed webhooks are replayed through the same inbox, so nothing depends on every webhook arriving.

## Data model

The core record is the **area review**: one row per MR per code area (or one per MR under a combined approval). Continuity, sessions, handovers and approvals hang off it, which is what lets a multi-area MR progress area by area.

| Entity | Key fields | Notes |
| --- | --- | --- |
| Person | GitLab user id, name, team, manager, working week, time zone | Synced from the directory; working week and holidays configurable per person |
| Team | Name, lead, lead's delegate | Reporting line decides who approves urgency |
| Code area | Name, path patterns, size limit, coverage policy | Path patterns seeded from CODEOWNERS |
| Ownership | Person, area, role (code owner primary or backup, merge owner) | A person can hold several areas and both roles |
| Coverage rule | Area or area pair, strict or combined | Organisation default plus overrides |
| Merge request | GitLab project and iid, author, state, urgent flag, opened at, queue age, size, release candidate | State follows the lifecycle in the proposal |
| Area review | MR, area(s) covered, current reviewer, state, approved by, approved at | Unit of continuity and handover |
| Review session | Area review, reviewer, date, time box, outcome (done, more time, hand back, missed) | The only time data kept |
| Capacity day | Person, date, organisation minimum, declared budget, calendar-adjusted capacity, reserve used | One row per reviewer per working day |
| Urgency request | MR, reason, requested by, approver, decision, overage hours | Reported per team |
| Handover | Area review, from, to, trigger, progress note, package link | Keeps priority and age |
| Author wait | MR, started, ended, reminders sent, reserve released at | Reported apart from review time |
| Merge assignment | MR, merge owner, assigned at, merged by, merged at | Merged by is checked against approvers |
| Release candidate | Name, MRs, state, notes, approved by, approved at | Notes generated, then editable |
| Inbox event | Source, event id, payload, received at, processed at | Idempotency for webhooks and replays |
| Audit event | Actor, action, subject, before, after, time | Append-only; feeds analytics |

Rule settings (thresholds, reserve share, coverage defaults) live in a versioned configuration table, so a change to a rule is itself audited.

## Integration contracts

Every external system sits behind an adapter interface, so a different calendar or chat product changes one adapter, not the domain modules.

| System | Direction | Mechanism | Used for | When it fails |
| --- | --- | --- | --- | --- |
| GitLab | In | Webhooks (merge request, pipeline, note, push) with a secret token | MR changes, approvals, comments, AI Reviewer signal | Reconciliation replays missed events |
| GitLab | Out | REST and GraphQL API with a service account | Set reviewers and assignees, read diffs and threads, post notes, merge (if decision 6 is taken) | Calls retried with backoff from the outbox; MR state unchanged until success |
| GitLab | Login | GitLab as OpenID Connect provider | Single sign-on to the web app | No login; chat actions still work |
| AI Reviewer | In, through GitLab | A configurable signal: a pipeline job status, an approval by its bot user, or a label | Stage 1 gate | MRs wait and keep their age; the agreed outage rule applies |
| Directory / HR | In | Scheduled sync (LDAP or HR export) | People, teams, managers, leave if held there | Last good copy kept; admin alerted |
| Calendar | In | Free/busy and leave lookup | Capacity per day, planned-leave handover | Booking falls back to declared budget only |
| Chat | Out and in | Bot messages with action buttons; callbacks to the API | Bookings, reminders, alerts, done / more time / hand back | Messages queued; the web app shows the same actions |

The orchestrator never writes code, never pushes commits, and never changes GitLab settings. Its service account needs only the scopes those calls require.

## Deployment

The orchestrator runs inside the company network beside GitLab, in a Kubernetes namespace or on Docker hosts, whichever the infrastructure team already runs. The only traffic that may leave the network is to a cloud chat service, through the existing egress proxy.

&#91;embedded content: Deployment view · on-premise\]

- Two replicas of the web app, API and worker cover a node restart. Only one worker runs the scheduler at a time, using a database lock.
- PostgreSQL runs with a standby and nightly backups, or on the company's existing database service if there is one.
- Environments: a staging copy points at a test GitLab group, so booking rules can be tried on real-looking MRs before they reach production.

## Cross-cutting concerns

**Security**

- Sign-in through GitLab single sign-on. Roles in the orchestrator (developer, code owner, merge owner, lead, CTO, admin) come from ownership and directory data, not from manual grants.
- Webhook calls are checked against a secret token and accepted only from the GitLab host.
- The GitLab service account holds the minimum scopes; its token and other secrets live in the cluster's secret store, never in configuration files.
- Admin actions (ownership, budgets, thresholds) are audited with before and after values.

**Reliability**

- Every inbound event is stored before it is processed, and processing is idempotent, so replays are safe.
- Outbound calls go through an outbox table and are retried, so a GitLab or chat outage delays actions but loses none.
- Only one worker runs the scheduler at a time, using a database lock; a second worker takes over if the first stops.
- The watchdog is the last line of defence: any MR with no state change past its threshold raises an alert.

**Explainability**

- Each booking, routing and handover stores the inputs and the reason ("primary owner, 2.5 h free on Monday, oldest in queue"). Reviewers and leads can see why an MR went where it did.

**Observability**

- Structured logs with the MR id on every line, health checks for the API and worker, and metrics for webhook lag, inbox backlog, outbox retries, queue depth per area and alerts raised.

**Privacy and trust**

- Only slot outcomes are stored. No activity tracking in GitLab, no keystroke or screen data.
- Reports are by area and team. Individual figures are visible to the person themselves and to admins for capacity planning, not ranked.

**Calendars and time**

- Working week, public holidays and time zone are set per person, so the company's own week and holidays drive booking rather than a fixed Monday-to-Friday assumption.

## Architecture decisions

Ten decisions shape the design. Decision 6 is the only one still open and needs a choice before Phase 2.

| # | Decision | Why | Rejected alternative |
| --- | --- | --- | --- |
| 1 | One codebase deployed as an API and a worker (modular monolith) | 20+ MRs a day is light load; one team can run and change it | Microservices: more moving parts with no scaling need |
| 2 | GitLab is the source of truth for code, approvals and merges; the orchestrator owns scheduling state | Avoids two systems disagreeing about who approved | Copying approvals into the orchestrator as their own truth |
| 3 | Webhooks plus periodic reconciliation, through an idempotent inbox | Webhooks can be missed; replays must be safe | Polling only (slow) or webhooks only (gaps) |
| 4 | PostgreSQL holds state, the job queue (outbox) and the audit log | One store to back up and operate | A separate message broker; revisit only if load grows |
| 5 | Booking rules are deterministic and store their reasons | Leads and reviewers can see and trust why an MR went where | An opaque optimiser or machine-learned scoring |
| 6 | **Open:** merges go through the orchestrator, which checks the merger is not an approver or the author, then merges with its service account | GitLab cannot stop an approver who has merge rights from merging | Detect only: let people merge in GitLab and alert on violations |
| 7 | The AI Reviewer is an opaque gate read from a GitLab signal | It is owned elsewhere; the orchestrator must not depend on its internals | Calling the AI Reviewer directly |
| 8 | CODEOWNERS seeds code areas; backups, merge owners and coverage rules are kept in the orchestrator | CODEOWNERS cannot express backups, merge owners or coverage policy | Maintaining ownership only in the orchestrator (drifts from the repo) |
| 9 | Every state change is an append-only audit event; analytics read from it | One history for audits, retrospectives and metrics | A separate analytics pipeline |
| 10 | Sign-in through GitLab single sign-on | Everyone already has a GitLab account; no new identity store | Separate accounts or a new identity provider |

On decision 6: with the enforced option, the protected branch allows only the orchestrator's service account to merge, and the orchestrator posts a note naming the person who merged. The detect-only option is simpler to start with but cannot guarantee the rule. The language and framework follow the team's main stack.

## Open questions and next steps

Six facts about the environment decide adapter and deployment details; none changes the overall structure.

- Which GitLab tier is running? Code owner approval rules and external status checks depend on it.
- Which calendar and chat products are used, and are they on-premise or cloud? Cloud chat needs the egress proxy.
- Is there a Kubernetes cluster for internal tools, or should this run on Docker hosts?
- How does the AI Reviewer publish its verdict in GitLab, and what happens to MRs when it is down?
- Where is leave recorded: the calendar, the HR system, or both?
- Decision 6: enforce separation of duties at merge, or detect only?

**Next steps**

- [ ] Answer the questions above with the GitLab admin and infrastructure team
- [ ] Agree the AI Reviewer signal with its owners
- [ ] Confirm the tech stack and repository for the orchestrator
- [ ] Build a thin slice: webhook ingress, MR lifecycle, area resolver and one booking rule against a test GitLab project
