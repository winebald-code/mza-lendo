<div align="center">

# Mzalendo

**Manufacturing operations and supply intelligence for African workshops.**

The floor reports on a feature phone. The office reads the whole plant on one screen.

Built for the Africa's Talking Open Hackathon — Manufacturing · Nairobi, 27 August 2026

</div>

---

## What this is

Most production software assumes every worker carries a smartphone with a data bundle and the
patience to learn an app. In a Kamukunji metal workshop or a Jua Kali shed, that assumption is
simply false — and it is the reason almost none of these businesses run production software at all.

Mzalendo inverts it. Workers interact through **USSD on any handset that can make a call**: no app,
no data, no training. Every entry they make — units completed, stock issued, a machine smoking, a
near miss — lands in the same database the owner's dashboard reads from.

On top of that sits the **Manufacturing Pulse**: one score for the health of the plant, assembled
from five weighted subsystems, together with a ledger explaining exactly what moved it and the one
action that would move it back.

### The two surfaces

| | Floor | Office |
|---|---|---|
| **Device** | Any feature phone | Browser |
| **Channel** | USSD + SMS | Web dashboard |
| **Needs data?** | No | Yes |
| **Session** | Under 30 seconds | As long as you like |

---

## Signature features

**Manufacturing Pulse** — Production (26%), Inventory (22%), Orders (22%), Maintenance (16%) and
Suppliers (14%) are each scored 0–100 from live records and weighted by how fast each one can stop a
plant. The result is rendered as a segmented analogue panel meter rather than a smooth donut,
because a factory gauge reads in notches. Critically, every point deducted comes with a **finding**:
what moved, why, and a link to the record that fixes it.

**Live USSD handset simulator** — A rendered feature phone inside the dashboard that posts to the
*real* `/ussd/callback` endpoint. There is no separate demo path, so anything you record through it
appears in the ledger, the schedule and the Pulse exactly as a genuine session would.

**Supplier reliability that is earned, not typed** — Scores are computed from what actually arrived
and when, across delivery timeliness, completeness and defect reports. Nobody can flatter a supplier
by editing a number.

**Shortage arithmetic from the bill of materials** — Order and run screens explode the BOM against
current stock and name the binding constraint: the single material that caps how many units you can
actually build today.

---

## Stack

- **Flask 3** + **SQLAlchemy 2** — single-file application (`app.py`, ~5,500 lines, 32 sections)
- **SQLite** by default; **PostgreSQL** automatically when `DATABASE_URL` is present
- **Tailwind CSS 3.4**, compiled at build time — no CDN, because a CDN would force `unsafe-eval`
  into the CSP
- **Vanilla JavaScript**, no framework. Charts are hand-rolled SVG
- **Africa's Talking** for USSD and SMS, with a full simulation mode when no API key is set

Runtime dependencies are listed in `requirements.txt` — every one resolves to a prebuilt wheel on
macOS, Linux and Windows for Python 3.11 through 3.13, so installing never needs a compiler or a
system library. The PostgreSQL driver lives separately in `requirements-postgres.txt`, because
local development does not need it. Node is used **only** to compile the stylesheet; nothing in the
running application needs it.

---

## Local setup

```bash
# 1. Clone and enter
git clone <your-repo-url> mzalendo && cd mzalendo

# 2. Python environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # SQLite only — no compiler needed

# 3. Configuration
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into SECRET_KEY

# 4. Build the stylesheet (needs Node 18+)
npm install
npm run build

# 5. Run
python app.py
```

Open <http://127.0.0.1:5000>. The database is created and seeded on first boot.

**If port 5000 is taken** — on macOS that is almost always the AirPlay Receiver — run on another
port instead:

```bash
PORT=5001 python3 app.py
```

The dev server binds `0.0.0.0`, so it is also reachable from your phone on the same network at the
LAN address it prints. That is the quickest way to see how the dashboard behaves on a small screen.
Set `HOST=127.0.0.1` to keep it local only.

> **Deploying against PostgreSQL?** Install `requirements-postgres.txt` instead — it pulls in
> everything above plus `psycopg2-binary`. You do not need it locally: `app.py` uses SQLite whenever
> `DATABASE_URL` is unset, and the driver is only imported when SQLAlchemy actually opens a Postgres
> connection. Railway and the Dockerfile install it for you.

### Working on the styles

```bash
npm run watch      # rebuilds static/css/app.css as you edit templates
```

`static/css/app.css` is generated and git-ignored. It must be built before the app is useful — an
unstyled Mzalendo is not a pretty sight.

---

## Signing in

A fresh install contains **one account and nothing else** — no sample plant, no demo materials, no
seeded workers. The first thing you do is create your plant.

### Bootstrap administrator

| | |
|---|---|
| **Username** | `winebald` |
| **Email** | `info@winebald.tech` |
| **Password** | `223011005@Winebald` |

Overridable with `SEED_ADMIN_USERNAME`, `SEED_ADMIN_EMAIL` and `SEED_ADMIN_PASSWORD` — set your own
before deploying anywhere public.

The account is created with `must_change_password` set: **it cannot reach any page except the
change-password screen until a new password is set**, so the temporary value works exactly once.

### A demonstration plant

Set `SEED_DEMO_DATA=1` to populate Kamukunji Metalworks on first boot — 13 materials, 5 products
with bills of materials, 5 machines, 6 workers, 6 orders, 5 runs and a fortnight of Pulse history.
Setting it back to `0` **removes** the demo plant and everything in it on the next boot, so one
variable flips a populated demo into a clean instance. Only the plant carrying the demo slug is
affected — real plants, their records and the administrator are never touched.

The administrator is reconciled from the environment on every boot, not just the first: if the
account was disabled, locked out by failed sign-ins, demoted, or left pointing at a deleted plant,
it is restored. The password is deliberately left alone once set, so rotating it in the interface is
not undone by the next deploy.

| Role | Email | Password |
|---|---|---|
| Owner | `owino@kamukunji.example` | `Kamukunji@2026!` |
| Manager | `wanjiku@kamukunji.example` | `Kamukunji@2026!` |
| Supervisor | `mutiso@kamukunji.example` | `Kamukunji@2026!` |

Worker handset PIN is `1234`.

### First run

1. Sign in and set a real password.
2. With no plants yet, you land on **All plants**. Create your first one.
3. Add users under **Users**, workers under **Workers** — a worker needs a phone number or the
   USSD menu will not recognise them.
4. Add materials, products and their bills of materials. The Pulse starts reading as soon as there
   are records to read.

---

## Trying the USSD flow

The fastest route is **Dashboard → Handset simulator**. Pick a worker, press Dial, and walk the
menu. To hit the endpoint directly:

```bash
curl -X POST http://127.0.0.1:5000/ussd/callback \
  -d "sessionId=test-1" \
  -d "serviceCode=*384*7788#" \
  -d "phoneNumber=+254711000201" \
  -d "text="
```

Responses follow the Africa's Talking convention: `CON` keeps the session open, `END` closes it.
Walk deeper by joining choices with `*` — `text=4*1*2*3` reports a critical fault on the first
machine, which stops the machine, opens a ticket and texts the managers.

| Option | Does |
|---|---|
| 1 | My tasks — runs assigned to this worker |
| 2 | Report production — updates a run and the schedule |
| 3 | Report stock — issued / received / counted / wasted |
| 4 | Machine fault — critical severity stops the machine |
| 5 | Safety incident |
| 6 | Clock in or out |
| 7 | Stock check — everything below minimum |

Workers can also skip the menu entirely and text `HELP`, `TASKS`, `STOCK`, `IN`, `OUT` or `DOWN`.

---

## Africa's Talking configuration

Without `AT_API_KEY` the app runs in **simulation**: every message is written to the message log
marked `simulated` and nothing leaves the building. The whole application is demonstrable in this
mode. To go live, set the credentials in `.env` (or per-plant under **Settings → Africa's Talking**,
which overrides the environment) and register these callbacks in your Africa's Talking dashboard:

| Purpose | URL |
|---|---|
| USSD | `https://your-domain/ussd/callback` |
| USSD events | `https://your-domain/ussd/events` |
| Delivery reports | `https://your-domain/sms/delivery` |
| Incoming SMS | `https://your-domain/sms/incoming` |
| Opt out | `https://your-domain/sms/optout` |
| Subscription | `https://your-domain/sms/subscription` |

The Settings screen renders all six with your actual domain filled in and a copy button beside each.

---

## Deploying to Railway

```bash
npm i -g @railway/cli
railway login
railway init
railway up
```

`railway.json` and `nixpacks.toml` are committed, so the build installs Python dependencies,
compiles the stylesheet and starts Gunicorn with a health check on `/healthz`.

Attach a **Railway Volume** mounted at `/data` before the first deploy — the Dockerfile
deliberately contains no `VOLUME` instruction, because Railway rejects it and persistence comes
from the platform volume instead. The container starts as root only long enough to take ownership
of that mount (Railway mounts volumes owned by root), then drops to an unprivileged user before any
application code runs.

Set these variables in the Railway dashboard:

```
SECRET_KEY=<a long random string>
FORCE_HTTPS=1
ALLOW_PUBLIC_SIGNUP=0
AT_USERNAME=<your Africa's Talking username>
AT_API_KEY=<your key>
AT_USSD_CODE=*384*7788#
SMS_ENABLED=1
```

Attach a Postgres instance and Railway injects `DATABASE_URL`; `app.py` detects it and switches
automatically, normalising the legacy `postgres://` scheme on the way.

> **On persistence:** without Postgres the app uses SQLite inside the container filesystem, which
> Railway replaces on every deploy. That is fine for a demo and wrong for anything real — attach
> Postgres, or mount a volume and point `DATA_DIR` at it.

---

## Docker

The image is multi-stage: Node compiles the stylesheet in stage one, and the runtime stage is slim
Python with no Node in it at all. Suitable for the isolated per-customer instances the marketplace
brief asks for.

```bash
docker build -t mzalendo:1.0.0 .

docker run -d -p 8000:8000 \
  -e SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" \
  -e FORCE_HTTPS=0 \
  -v mzalendo-data:/data \
  --name mzalendo mzalendo:1.0.0
```

Runs as an unprivileged user (uid 10001), stores its database in the `/data` volume, and carries a
`HEALTHCHECK` against `/healthz`.

---

## Security

Verify any of this against a running instance with `curl -I` or an external header scanner.

| Header | Value |
|---|---|
| `Content-Security-Policy` | `default-src 'self'` with a **per-request nonce**; no `unsafe-inline`, no `unsafe-eval` |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains; preload` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | Camera, microphone, geolocation, payment and the rest disabled |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `Cache-Control` | `no-store` on every authenticated route |

`Strict-Transport-Security` and CSP's `upgrade-insecure-requests` are sent only when the connection
is actually secure — `request.is_secure`, or `X-Forwarded-Proto: https` from a terminating proxy —
or when `FORCE_HTTPS=1`. Sending `upgrade-insecure-requests` over plain http would tell the browser
to re-fetch every stylesheet and script over TLS against a server that does not speak it; browsers
exempt `localhost` but not a LAN address, so the page would load unstyled. `FORCE_HTTPS` therefore
defaults to **off**, since turning it on also marks the session cookie `Secure` and a browser will
not store that over http — nobody could sign in locally. **Set `FORCE_HTTPS=1` on every real
deployment**; the app logs a warning at startup when it is off.

Because `style-src` carries a nonce and no `unsafe-inline`, a server-rendered `style=""` attribute
would be **blocked outright**. Values that genuinely must be computed per row — bar widths, gauge
sizes, Gantt column spans — are emitted as `data-css-*` attributes and applied through the CSSOM,
which CSP does not govern. The policy stays strict and the bars still draw. There are zero inline
style attributes in the rendered HTML.

Beyond headers:

- **Passwords** hashed with a salted one-way function; 12 characters minimum with upper, lower,
  digit and symbol required
- **Account lockout** after 6 failed attempts, for 15 minutes
- **Forced password rotation** on every seeded or reset credential
- **CSRF tokens** on all state-changing forms; telco callbacks are exempt by necessity and instead
  rate-limited and validated
- **Tenant isolation** — every query is scoped to a plant through a single `scoped()` helper
- **Five roles** from viewer to super administrator
- **Rate limiting** per address, tighter on sign-in and callbacks
- **Audit trail** covering every meaningful change, not editable from the interface
- **No third-party scripts** — no analytics, no tag manager, no CDN except font files

---

## Project layout

```
mzalendo/
├── app.py                     # the entire application, 32 numbered sections
├── requirements.txt           # core — pure wheels, no compiler
├── requirements-postgres.txt  # core + psycopg2, for server deployments
├── package.json               # stylesheet build only
├── tailwind.config.js
├── Procfile / railway.json / nixpacks.toml / Dockerfile
├── .env.example
├── static/
│   ├── css/input.css          # source; app.css is generated
│   ├── js/app.js              # interface behaviour
│   ├── js/charts.js           # hand-rolled SVG charts
│   └── img/                   # logo and favicon
└── templates/
    ├── base.html              # public shell
    ├── partials/              # icons (60 hand-drawn), logo, flash
    ├── public/                # home, platform, pricing, jua kali, security
    ├── auth/                  # login, signup, change password
    ├── errors/                # 404 and general error
    └── dash/                  # _layout, _components (macros) + 45 screens
```

### `app.py` sections

| | | | |
|---|---|---|---|
| 1 Config | 9 Filters | 17 Orders | 25 Settings & admin |
| 2 Extensions | 10 Public routes | 18 Production | 26 USSD |
| 3 Security headers | 11 Auth | 19 Machines | 27 SMS callbacks |
| 4 Constants | 12 Overview | 20 Quality | 28 JSON API |
| 5 Models (24) | 13 Alerts | 21 Workforce | 29 Error handlers |
| 6 Helpers | 14 Materials | 22 Messaging | 30 Seeding |
| 7 Africa's Talking | 15 Procurement | 23 Reports | 31 CLI |
| 8 Pulse engine | 16 Products | 24 Users | 32 Entrypoint |

---

## Operational notes

Three things that bite on a real deployment rather than a laptop:

- **`/healthz` is exempt from the HTTPS redirect.** Platform health probes reach the container
  directly over http on the private network. Redirecting them makes a healthy container look dead
  and the deploy never goes green. The route returns no session data and sets no cookie.
- **Static assets carry a content fingerprint** (`app.js?v=<sha256 prefix>`) and are served
  `immutable` for a year, while pages are `no-store`. Without this, one browser keeps running the
  cached script after a deploy while another fetches the new one — and only one of them shows the
  bug.
- **The port is read in Python, not interpolated by a shell.** `gunicorn.conf.py` reads `PORT`
  from the environment, so the start command contains no `$PORT`. A platform that runs the start
  command directly rather than through a shell would otherwise hand gunicorn the literal characters
  `$PORT` and it refuses to start.
- **Schema creation is serialised across workers.** Every worker imports the app, so without a lock
  they race on `create_all()`; one wins and the others exit code 3, which the platform reports as
  "service unavailable".
- **The rate limiter counts per process.** The default is two Gunicorn workers, so the effective
  limit is twice what the configuration says. Drop to `--workers 1`, or point Flask-Limiter at
  Redis, before the numbers need to be exact.

USSD screens are capped at 182 characters — one GSM payload — at `ussd_response()`, the single
point every screen leaves through. Anything longer is truncated by the telco, which cuts a menu
line in half and leaves the worker with an option they cannot read. Call sites trim individual
labels, but plant names, worker names and material names are all free text, so the guarantee is
enforced at the boundary: whole options are dropped and the trailing navigation line is preserved.

---

## Time

Timestamps are stored in UTC and displayed in the plant's local zone, which also decides where the
day boundary falls for attendance, "due today" and the schedule. It defaults to **EAT (UTC+3)** —
Kenya has never observed daylight saving, so a fixed offset is exact and avoids depending on the
`tzdata` package being installed.

```
TZ_OFFSET_HOURS=3
TZ_LABEL=EAT
```

Set both for a plant in another zone. Storage is unaffected, so changing it re-renders history
correctly rather than corrupting it.

---

## CLI

```bash
flask --app app init-db                 # create tables
flask --app app reset-db                # drop, recreate, reseed  (destructive)
flask --app app create-admin            # interactive super administrator
flask --app app pulse                   # print the Pulse for every plant
```

---

## Design

The interface is built on an industrial light system: **Fog** `#F2F4F6` ground, **Ink** `#07090B`
text, **Signal** orange `#FF6A00` for anything that demands attention. Type is Space Grotesk for
display, Inter for body and IBM Plex Mono for codes and telemetry — the mono is not decoration, it
is what USSD strings and part codes look like on a feature phone.

Roughly sixty icons are hand-drawn SVG symbols in a single sprite. No icon font, no emoji, no
stock illustration.

---

## Licence

MIT.

<div align="center">

Telco layer powered by **Africa's Talking** · Built in Nairobi

</div>
