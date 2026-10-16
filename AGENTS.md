# frappe_paystack (fork) — Agent Instructions

This is the **fork the live deployment actually runs** — not the upstream app. `apps.json`
still pins upstream (`mymi14s/frappe_paystack`); this fork is overlaid on top of it at image
build time. Knowing that saves an hour of confusion.

## Where the setup is

| | |
|---|---|
| Production host | `contabovps` (`vmi1691344.contaboserver.net`), ssh alias `breamal_server` |
| Deployment dir | `/opt/frappe_docker` on that host |
| Site | `frontend` — https://erp.breamal.com |
| How this fork reaches production | `images/custom-ext/Containerfile` fetches `https://github.com/rockson-waters/frappe_paystack/archive/<commit>.tar.gz` at build time and copies it over the pinned upstream app. The commit is the `PAYSTACK_REF` build arg. |
| Operating docs | `/opt/frappe_docker/UPGRADE-CHECKS.md` (Issues 2 and 3 are this app), `PAYSTACK-FORK-CHANGES.md`, `verify-erp.sh` |

**Full orientation is at `/home/rockson/repos/server/AGENTS.md`** — how to reach the host
(there is an ssh-config quirk), how to run Python against the site, build and verify
commands, and the traps worth knowing: Frappe's caching, the GHS/CAD currency rounding,
the client IP never surviving the proxy chain, and the site timezone. Read it before
touching the deployment.

## Rules

- **This repo must stay PUBLIC.** The image build fetches it anonymously over HTTPS. If it
  is private the build dies with `curl` exit 22 (HTTP 404) at the overlay step — that
  happened once, and the workaround someone reached for (copying app code into the running
  container) broke the whole desk.
- **Never test against production.** Settle the change here, push it, then rebuild and
  overlay. This app's test suite mutates shared records — gateway accounts, Mode of Payment,
  POS Settings — so `bench --site frontend run-tests --app frappe_paystack` is not safe on a
  live site.
- **A push alone changes nothing in production.** The overlay pins a **commit**. After
  changing this app: push, bump `PAYSTACK_REF` in
  `/opt/frappe_docker/images/custom-ext/Containerfile`, rebuild stage 2
  (`docker build --tag=custom:16 --file=images/custom-ext/Containerfile .`),
  `docker compose up -d`, then run `/opt/frappe_docker/verify-erp.sh`.
- The overlay exists because re-pinning `apps.json` means a full stage-1 rebuild, which also
  advances `frappe`, `erpnext` and `payments` to their branch tips. If you ever do re-pin,
  delete the overlay job — otherwise the app has two sources of truth.

## Changes this fork currently carries

Both are also recorded in `/opt/frappe_docker/UPGRADE-CHECKS.md`:

1. **Issue 2** — `register_as_payment_gateway()` no longer calls ERPNext's
   `payment_gateway_enabled` hook. It was called without a company, so ERPNext fell back to
   the default company (a different legal entity here) and emitted a false
   *"Payment Gateway Account not created, please create one manually."*
2. **Issue 3** — `build_payment_request()` refuses a missing gateway account *before*
   `make_payment_request()` writes anything, and repoints a reused gateway-less draft. A
   failed attempt could previously strand a draft that every later attempt reused, wedging
   the payment button permanently behind a message that blamed the gateway account.

`frappe_paystack/tests/` carries regression tests for both, added in the commit after the
fixes. They have never been executed — the only site available disallows tests, and the
suite is unsafe there. Run them on a scratch site:

```sh
bench --site <scratch> run-tests --app frappe_paystack
```
