# VisibilityForge AI

VisibilityForge AI is a Flask AEO, SEO, and GEO visibility intelligence workspace. It includes a responsive landing page, a public static read-only demo, and an authenticated database-driven workspace for recorded questions and measurements, technical audit issues, content pages, citation opportunities, activity, and 90-day tasks.

## Architecture and technology

- Python 3.11+ (the workspace test run uses Python 3.12.8), Flask application factory, Flask-Login, Flask-WTF/CSRF, Flask-SQLAlchemy, and Flask-Migrate/Alembic.
- PostgreSQL with Psycopg 3 is required in production. Local development defaults to an instance-relative SQLite file; automated tests use isolated SQLite.
- Server-rendered Jinja templates, Bootstrap 5 from jsDelivr, project-owned CSS/JavaScript, and Chart.js for database-backed dashboard charts.
- Blueprints divide the application into public/health/auth, dashboard, research, technical audit, content, citations, and reports. The public `/demo` page uses static illustrative copy and does not read workspace records. Workspace pages and all write actions remain login-protected. Services contain the crawler, visibility calculations, question CSV handling, content drafting, measurement adapter interface, and report calculations.
- Every user-owned query is scoped to the signed-in user. Forms use CSRF protection; response headers include a nonce-based Content Security Policy and browser security headers. Login failures use persistent, hashed email/IP rate-limit buckets in the database.
- Migrations are committed and applied with Flask-Migrate. Review each migration for data impact; deployments must not use `db.create_all()`, schema resets, or destructive migration shortcuts.

## Project structure

```text
app/
  __init__.py                application factory, middleware, CLI, error handlers
  config.py                  environment-backed development/testing/production config
  extensions.py              SQLAlchemy, Migrate, LoginManager, CSRF
  forms.py                   validated WTForms
  models/entities.py         tenant-scoped SQLAlchemy models and constraints
  routes/                    authenticated module blueprints
  services/                  reporting, visibility, content, import, audit, adapters
  templates/                 Jinja pages, forms, and error states
  static/css/                responsive application styles
  static/js/                 navigation and interaction scripts
migrations/                  Alembic environment and versioned schema changes
tests/                       route, model, workflow, security, and service tests
instance/                    local-only SQLite development database
.env.example                 placeholders only
render.yaml                  Render Blueprint
requirements.txt             pinned runtime and test dependencies
run.py                       local Flask entry point
```

## Requirements

- Python 3.11 or a compatible supported Python version (Python 3.12 is supported)
- PostgreSQL for production deployments
- PowerShell on Windows

## Set up locally (Windows PowerShell)

Run these commands from the project directory. When Python 3.11 is installed, use:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If Python 3.11 is unavailable but Python 3.12 is installed, replace `py -3.11` with `py -3.12`.

Generate a local secret key and copy the output into `SECRET_KEY` in `.env`:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

For development only, if `SECRET_KEY` is unset, the application generates a temporary random key so CSRF-protected forms such as sign-in work locally. Set a stable key in `.env` if you need sessions to survive application restarts. Production still requires an explicitly configured secret.

The default development configuration uses a local SQLite database at `instance/visibilityforge-dev.db`. To use PostgreSQL locally, set `DATABASE_URL` in `.env` to your PostgreSQL connection URL. Set `OWNER_EMAIL` and `INITIAL_ADMIN_EMAIL` to the intended workspace contact and administrator addresses; the defaults are placeholders (`owner@example.com` and `admin@example.com`). Choose a long, unique `INITIAL_ADMIN_PASSWORD` locally. Never commit `.env` or share its contents. The application loads `.env` automatically when run through Flask or `python run.py`.

## Run the application

```powershell
.\.venv\Scripts\Activate.ps1
python run.py
```

Open <http://127.0.0.1:5000>. The health check is at <http://127.0.0.1:5000/health>; it runs a read-only database check and returns 503 when the database is unavailable.

## Public read-only demo

Open <http://127.0.0.1:5000/demo> to preview the product without an account. The deployed demo is <https://visibilityforge-ai.onrender.com/demo>. The page is static, contains only illustrative descriptions, and does not query the database or expose saved user records. It does not collect data from search engines or AI platforms. The database-backed dashboard and every research, audit, content, citation, and reporting workflow remain protected by authentication.

The public demo includes a 2:58 product walkthrough video at [the public GitHub release](https://github.com/Chandru7560/visibilityforge-ai/releases/tag/product-walkthrough-2026-10-31). The published video is a compressed copy with the account email blurred; the original local recording is unchanged.

## Database setup and initial administrator

Apply the committed schema migration before signing in:

```powershell
flask --app run.py db upgrade
```

Create the initial account from the environment variables in `.env`:

```powershell
flask --app run.py create-admin
```

There is no public registration route. The `create-admin` command requires `INITIAL_ADMIN_EMAIL` and `INITIAL_ADMIN_PASSWORD`, hashes the password, and refuses to replace an existing account. Sign in at <http://127.0.0.1:5000/auth/login>, then open the protected dashboard at <http://127.0.0.1:5000/dashboard/>. The configured owner email is a contact address only; no email provider or email delivery is configured.

You can also use Flask's development server:

```powershell
flask --app run.py run --debug
```

## Run tests

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest
```

Tests use an isolated in-memory SQLite database and do not require a running PostgreSQL server.

## Database migrations

Flask-Migrate is initialized by the application factory. The initial schema migration is committed. After adding or changing SQLAlchemy models, create and apply a migration:

```powershell
flask --app run.py db migrate -m "Describe schema change"
flask --app run.py db upgrade
```

Commit generated migration files so deployments can apply the same schema changes. Import new models from `app.models` so Alembic can detect their metadata.

## Configuration

| Variable | Purpose |
| --- | --- |
| `APP_ENV` | `development`, `testing`, or `production`; defaults to `development`. |
| `SECRET_KEY` | Flask session signing key. Set a randomly generated value locally and a managed secret in production. |
| `DATABASE_URL` | SQLAlchemy database URL. Supports PostgreSQL URLs; legacy `postgres://` and plain `postgresql://` URLs are normalized to the installed Psycopg 3 driver. |
| `LOG_LEVEL` | Python logging level; defaults to `INFO`. |
| `OWNER_EMAIL` | Account contact address; defaults to the placeholder `owner@example.com`. Contact only; no email is sent. |
| `INITIAL_ADMIN_EMAIL` | Email used by the one-time `create-admin` CLI command. |
| `INITIAL_ADMIN_PASSWORD` | Password for the initial administrator; never commit this value. |
| `AUDIT_MAX_PAGES` | Default maximum HTML pages per technical audit (default 25; user input is always capped at 50). |
| `AUDIT_MAX_DEPTH` | Default internal-link depth (default 2; hard cap 3). |
| `AUDIT_REQUEST_INTERVAL` | Minimum delay between outbound HTTP requests, including redirects (default 1 second; hard minimum 0.5 seconds). |
| `AUDIT_REQUEST_TIMEOUT` | Per-request timeout in seconds (default 3; hard maximum 3 seconds). |
| `AUDIT_MAX_RESPONSE_BYTES` | Maximum response body size in bytes (default 512,000; hard maximum 2,000,000). |
| `AUDIT_MAX_DURATION` | Total audit time budget in seconds (default 20; hard maximum 25). |
| `CONTENT_LLM_API_KEY` | Optional secret for the configured OpenAI-compatible content drafting provider. Leave unset to use templates. |
| `CONTENT_LLM_API_URL` | HTTPS chat-completions endpoint (default `https://api.openai.com/v1/chat/completions`). |
| `CONTENT_LLM_MODEL` | Model name sent to the configured content provider (default `gpt-4o-mini`). |
| `CONTENT_LLM_TIMEOUT` | Content-provider request timeout in seconds (default 15; application bounds it to 1–30 seconds). |
| `LOGIN_RATE_LIMIT_ATTEMPTS` | Failed attempts per hashed email bucket in a rolling fixed window (default 5). |
| `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | Login rate-limit window in seconds (default 900). |
| `LOGIN_RATE_LIMIT_IP_ATTEMPTS` | Failed attempts per client-IP bucket in that window (default 30). |

Production startup fails with a clear configuration error if `SECRET_KEY` is unset or `DATABASE_URL` does not point to PostgreSQL. Production sessions use secure, HTTP-only, `SameSite=Lax` cookies. Render's trusted proxy headers are handled using Werkzeug `ProxyFix` with one trusted proxy; do not expose the production Gunicorn service directly to untrusted clients outside Render's edge. A nonce-based CSP, `X-Content-Type-Options`, frame protections, Referrer-Policy, and Permissions-Policy are set on responses. HSTS is enabled in production. Do not use the test-only configuration outside automated tests.

## Dashboard and Research Hub

The authenticated dashboard and Research Hub use only the signed-in user's database records. Empty states are shown instead of estimated statistics. Dashboard trend charts use stored 0–100 visibility scores from the last 90 days; audit issues and 90-day tasks are based on saved audit and task records.

Open **Research** in the application navigation to:

- Create, edit, view, filter, archive, and restore questions; detect duplicates; and import/export question CSV files. Question categories include AI content creation, editing, repurposing, captioning, publishing, content growth, comparisons, and tool recommendations.
- Attach actual source URLs and record source types (search suggestions, keyword-tool results, forums, community discussions, People Also Ask, or other). A verified question must have a source URL; the application does not discover or independently verify sources.
- Record repeat observations for the United States, United Kingdom, Canada, Australia, or India. Each measurement preserves its question, exact observed query, market and location setting, local collection time and time zone (stored in UTC), search/assistant profile, collection method, observed result, mention assessment, citations, competitor evidence, optional Google rank and answer-box fields, notes, errors, and evidence reference.
- Review paginated measurement history and per-measurement evidence audit details, and export measurements as `visibility.csv`.

ChatGPT, Gemini, Perplexity, Claude, Copilot, Google Search, and Google AI Overviews are supported as recording profiles. **No external collection API is configured.** The current workflow is explicitly manual or evidence import; saving an observation does not query an assistant or search engine. Do not bypass platform logins, CAPTCHAs, or restrictions. Collection failures are recorded separately from observed answers and do not count as negative brand mentions. Measurements are append-only in the application: repeat checks add new records instead of replacing earlier evidence.

### Research CSV formats

Question imports require the following UTF-8 CSV headers (additional columns are ignored):

```csv
question,topic,source_url,source_type,market,language,verification_status
```

Imports are limited to 2 MB and 1,000 questions per file, reject duplicate questions and malformed rows atomically, and do not mark a question verified unless the supplied status is `verified` and it has a source URL. Use only questions and source references you actually checked.

The `visibility.csv` export has a stable, header-first schema:

```text
measurement_id,question,topic,source_url,source_type,verification_status,collection_date_utc,location,market,engine,collection_method,query,observed_result,mention_status,mention_context,sentiment,cited_urls,competitor_names,competitor_evidence,google_rank,featured_snippet,ai_overview,evidence_reference,collection_status,error_status,notes
```

Spreadsheet-formula prefixes in user-provided text fields are escaped in CSV exports.

### Visibility metric definitions

- **Brand mention rate:** assessed observations marked `mentioned` divided by assessed observations. `not_assessed` and failed collections are excluded, not treated as negative results.
- **Brand citation rate:** assessed observations marked `mentioned` with one or more recorded cited URLs divided by all assessed observations.
- **Competitor frequency:** count of successful or incomplete observations in which each competitor was recorded; this is a count, not a market-share estimate.
- **Question evidence coverage:** active questions with a saved evidence reference divided by active questions. With no active questions, coverage is unavailable rather than 0%.
- **Market/engine breakdown:** shows all observation attempts and the separately assessed sample; rates use the assessed sample only. Question coverage counts distinct questions with a non-failed observation.
- **Period change:** compares the earlier and later chronological halves of assessed observations in the selected sample, with each half's sample size displayed. It is not a calendar-period or causal comparison.

All reported rates and counts are calculated from stored records. They are descriptive of the entered evidence only and should be interpreted alongside sample size and coverage.

## Technical SEO audits

Open **Technical SEO** in the authenticated navigation. Submit a public website URL, a maximum page count and link depth, and confirm you are authorized and have reviewed that site's terms. Audits are synchronous and intentionally small: defaults are a configured cap of 25 HTML pages, depth 2, a 1-second minimum between HTTP requests, 3-second request timeout, 512 KB response limit, and 20-second overall budget. Deployments can lower or raise the configured page/depth/size/timeout values within hard caps of 50 pages, depth 3, 0.5 seconds between requests, 2 MB per response, 3 seconds per request, and 25 seconds overall. Supporting requests for robots.txt, a sitemap, llms.txt, and (for HTTP inputs) HTTPS availability are additional to the HTML page count. A crawl may stop early when robots rules, errors, or the time budget prevent further requests.

The crawler uses direct HTTP(S) connections, validates every DNS resolution and pins the connection to a public address, rejects private/reserved IPs, local/internal hostnames and nonstandard ports, and rechecks every redirect destination. HTTPS redirects cannot downgrade to HTTP. Only redirects on the entered hostname and allowed by the fetched robots policy are followed. Pages disallowed by robots.txt are not fetched. A critical robots.txt failure (server error, authorization denial, or rate limit) stops crawling conservatively. The crawler sends a descriptive `VisibilityForgeAuditBot/1.0` user agent and does not use proxies, browser automation, authentication, or CAPTCHA workarounds. The URL allowlist does not replace authorization or terms review.

Each audit stores its history, page observations, issues, evidence, and recommendations. The report checks observed HTTP status, redirects, title presence/length/duplicates, meta descriptions, canonical presence, H1s, viewport metadata, same-origin links and broken links within crawl coverage, sitemap discovery, robots.txt response, HTTPS availability, JSON-LD presence and JSON syntax, image alt attributes, response bytes/timing, and `/llms.txt`. Size and timing thresholds are simple audit heuristics, not field-performance measurements. Internal links beyond the page/depth/time budget are left unverified and are not called broken. An absent optional convention (such as JSON-LD or llms.txt) is reported as an observation, not proof of a ranking defect.

Reports include 20 prioritized recommendations. Recommendations linked to this crawl are marked **Observed** and contain crawl evidence; any remaining entries are marked **Proposed**, not detected problems. Code examples are illustrative. An issue is never described as externally validated by code generation. The report's local validator checks JSON-LD JSON syntax or the presence of basic HTML title, description, and canonical metadata; it does not validate schema semantics or Google's eligibility. No external validator or Search Console integration is configured. **Indexing status remains unknown** unless checked in an authorized indexing source; fetching a page is not evidence that Google indexed it.

Audit findings can be filtered and sorted, opened as detailed evidence records, and downloaded as CSV. Export columns are: `audit_id,root_url,page_url,category,severity,title,evidence,explanation,suggested_correction,code_example,validation_method,validation_status,timestamp_utc`. Spreadsheet-formula-prefixed values are escaped. The crawler is not a full browser renderer and does not execute JavaScript; its HTML checks reflect the bounded response body received at crawl time.

## Content Studio

Open **Content Studio** to draft how-to guides, product comparisons, FAQ pages, glossary entries, and content briefs. A brief accepts the target question, keyword, search intent, market, audience, source URLs, internal links, and optional links to an owned Research Hub question or technical audit recommendation. Drafts include SEO title, meta description, slug, a structured Markdown body, source/internal-link suggestions, a quality checklist, verification warnings, and FAQPage or HowTo JSON-LD when applicable. JSON-LD is assembled locally from the selected format; review the markup before using it.

Template-based drafting is the default and makes no external AI call. It does not claim to be AI-generated, independently verify user-supplied URLs, or establish product facts. Optional LLM mode requires `CONTENT_LLM_API_KEY`; it uses the configured OpenAI-compatible chat-completions endpoint and model. The brief and provided source URLs are sent to that provider only when LLM mode is explicitly selected. Configure the key and endpoint only for a provider you trust. Missing keys result in an explicitly labelled template draft. Provider timeouts, rate limits, authentication errors, other HTTP failures, and malformed responses are surfaced; the application does not report those requests as successful drafts.

Drafts can be edited, previewed, duplicated, archived, and exported as Markdown; generated schema can also be exported as JSON-LD. A status of “Marked published” is only a local record and does not publish to a website. Generated drafts remain drafts, and their checklist requires a human to verify product capabilities, pricing, availability, comparisons, and source support. The **Generate up to 10** action creates at most ten template FAQ drafts from active questions that are marked verified and have a source URL, excluding questions that already have a non-archived draft. The application does not seed fake URLs or fabricate the ten source records; if fewer eligible questions exist, fewer drafts are created with an explanatory message.

## Citation Opportunity Planner

Open **Citations** to manually record and manage a set of at least 30 real third-party prospects (the workspace is not capped at 30), including articles, directories, comparison pages, review sites, and community discussions. Each opportunity can link to a research question and stores relevance evidence, an evidence URL, any verified competitor reference and its evidence URL, a suggested legitimate contribution angle, priority, research status, and notes. Marking an opportunity researched requires relevance evidence; a competitor reference requires a separate evidence URL. Saving a prospect never means that a publisher has mentioned IncuBrix or a competitor, and the planner does not automatically fetch or research third-party pages.

The planner supports search, source-type/status/priority filters, paginated results, detail and edit views, and filtered `citation-opportunities.csv` export. The CSV schema is `opportunity_id,source_name,url,source_type,target_question,relevance_evidence,evidence_url,competitor_reference,competitor_evidence_url,suggested_angle,priority,research_status,notes,created_at_utc,updated_at_utc`; spreadsheet-formula-prefixed values are escaped. No third-party opportunity records are pre-seeded because no verified publisher URLs were supplied.

## Weekly reporting and 90-day launch plan

Open **Reports** to generate a report for an inclusive date range, review the stored report history, refresh the same range in place, download `weekly-report.json`, or view the readable HTML report. Reports calculate tracked and source-verified questions, measurement attempts and coverage by engine/market, assessed IncuBrix mention and citation rates, recorded competitor frequency, technical issue severities, content records created and marked published, researched citation opportunities, and failed measurement/audit attempts. Each metric includes its formula, sample size, supporting records, and an assessment evidence index with links to the underlying records.

Mention and citation rates exclude failed and unassessed observations; an empty assessed sample is **Not available**, not 0%. The citation rate is the assessed-measurement rate of recorded citations, not a claim that every cited URL was independently verified. Comparison uses the immediately preceding period of equal duration and is descriptive only; it does not establish cause or SEO improvement. Question counts use creation time, measurements use collection time, audit issues use issue creation time, and status-based content/citation counts use their documented record timestamps and current status. “Marked published” indicates an application status only, not a verified live URL.

Authenticated report exports are available at `/reports/exports/`:

| File | Contents |
| --- | --- |
| `visibility.csv` | Measurement and question/source evidence, observed result, assessment, competitor data, and collection state |
| `technical-audit.csv` | Stored audit issues, evidence, recommendations, and validation status |
| `citation-plan.csv` | Citation opportunities and their research evidence/status |
| `content-pages.csv` | Content metadata, source/link references, status, and timestamps |
| `growth-plan.csv` | Planning weeks, task owner/tools/budget, suggested targets, risks, actual outcome, and status |
| `weekly-report.json` | Download from a report's JSON action; contains the report metrics, formulas, evidence index, and comparison |
| Readable HTML | Download/print-friendly authenticated report detail page |

CSV exports use stable headers and escape spreadsheet-formula-prefixed values. Reports are tenant-scoped to the authenticated user. Run a report manually from the UI or from PowerShell/another deployment command:

```powershell
flask --app run.py reports generate --user-id 1 --start-date 2026-10-01 --end-date 2026-10-07
```

The CLI updates the unique report record for the same user/date range instead of duplicating it; the database uniqueness constraint also protects concurrent creation. No in-process scheduler is started, avoiding multiple schedulers across web workers. To automate reports, configure a single external scheduler/worker (for example, one Render cron job) to invoke the command with the intended user ID and dates. The command records completion/failure status and calculation duration; failures are logged and surfaced without returning stack traces to the UI.

The **90-day plan** presents 13 Monday-to-Sunday planning periods (91 days), with editable tasks, owners, tools, optional budgets, suggested targets, risks, status, and a separate actual-outcome field. Initialize suggested weekly tasks once and edit them to fit the project; suggestions are not achievements or measured outcomes. Budget and owner fields begin blank. A reusable brief generator at `/reports/content-brief` saves an answer-first content brief to Content Studio, including audience, intent, supporting questions, source requirements, links, schema recommendation, and quality checks. It is template-generated and does not verify claims or sources.

## Deploy to Render

The committed Blueprint defines the Flask web service, a Render PostgreSQL database, generated `SECRET_KEY`, PostgreSQL `DATABASE_URL`, Python runtime, Gunicorn command bound to Render's `$PORT`, and `/health` readiness check. The endpoint performs a read-only database query and returns 503 if the database is unavailable. The start command applies committed Alembic migrations before serving. Migrations are additive/versioned; review and test new migrations against a copy of production data before deploying them.

**The current Blueprint uses Render Free plans for assessment/demo use. Render explicitly warns not to use Free instances for production, and free web filesystems are ephemeral. Before storing real assessment/customer data, choose paid persistent web and PostgreSQL plans in Render and confirm database backup/retention settings.** User data is stored in PostgreSQL, not the web service's filesystem; local SQLite is for development only.

1. Push the code to a GitHub repository using the instructions below.
2. In Render, choose **New → Blueprint**, connect the GitHub repository, and review `render.yaml`. Ensure the web service and database are in a compatible region; use the database's internal connection string.
3. Before first account creation, set `INITIAL_ADMIN_EMAIL` to the intended administrator address and configure a strong `INITIAL_ADMIN_PASSWORD` as a secret in the Render service environment; verify the generated `SECRET_KEY` and PostgreSQL `DATABASE_URL`. The committed email values are placeholders. Do not put credentials in `render.yaml`, GitHub, or logs.
4. Create the Blueprint/deploy. Render builds from `requirements.txt`; the start command applies migrations, runs the idempotent `bootstrap-admin` command, and then starts Gunicorn. The bootstrap creates an account only when the configured email is absent and a password is set; it never changes an existing account. Check the deploy logs and open `/health`.
5. After the first administrator has been created, remove `INITIAL_ADMIN_PASSWORD` from the Render service environment. The bootstrap safely skips an existing administrator on later starts. No public registration or email delivery is implemented.
6. Confirm the login, dashboards, and database persistence on the public URL; test a new migration in a staging database before production deploys.

For production scaling, move migration execution to a single deployment/release step supported by the selected Render plan before adding multiple instances; do not let every web instance race to migrate the same database. This service has no in-process scheduler.

### Push to GitHub

Create an empty GitHub repository first. From PowerShell in the project directory:

```powershell
git init
git branch -M main
git add .
git status --short
git diff --cached --name-only
git commit -m "Prepare VisibilityForge AI release"
git remote add origin "https://github.com/YOUR_GITHUB_USERNAME/visibilityforge-ai.git"
git push -u origin main
```

Replace the owner/repository in the remote URL. Review staged files before committing: `.env`, databases, virtual environments, caches, and the supplied assessment PDF are ignored. Authenticate with Git Credential Manager or GitHub CLI; never put a personal access token in a remote URL or source file. If the repository already has a Git remote, inspect it with `git remote -v` and do not add a duplicate.

## Database backups and recovery

Use Render PostgreSQL's configured backup/PITR retention for production and verify the current plan supports the required retention. Keep backups encrypted and access-controlled, and periodically test restore into a separate database. A manual custom-format backup can be taken from a trusted machine with PostgreSQL client tools and `DATABASE_URL` set in its environment:

```powershell
pg_dump --format=custom --file=visibilityforge-backup.dump --dbname="$env:DATABASE_URL"
```

Store backup files outside the repository and verify they do not contain public credentials. Restores should target a fresh/staging database first. Local SQLite data is not a production backup strategy.

## Troubleshooting

- **Production fails at startup:** set a high-entropy `SECRET_KEY` and a PostgreSQL `DATABASE_URL`; SQLite is rejected in production.
- **Database tables are missing:** run `flask --app run.py db upgrade` from the deployed release environment and inspect migration logs. Do not run `db create_all()` in production.
- **PostgreSQL connection errors:** use Render's internal database URL when services share a region, ensure the database is running and accessible, and verify its credentials are configured only as environment variables.
- **Login returns 429:** failed attempts are stored as hashed email/IP buckets for 15 minutes by default. Wait for the window to expire; adjust the `LOGIN_RATE_LIMIT_*` environment values only with a deliberate security review.
- **Import returns 413:** question CSV upload is capped at 2 MB; split the file into smaller batches of at most 1,000 questions each.
- **Content generation uses a template:** `CONTENT_LLM_API_KEY` is unset or template mode was selected. Configure a provider key only in a secret environment variable; external requests are optional.
- **Technical audit refuses a URL:** only standard-port public HTTP/HTTPS destinations are accepted; private addresses, redirects outside safe scope, robots-disallowed paths, and sites without authorization are not crawled.
- **Charts are unavailable:** charts require stored measurements/issues and the configured Chart.js CDN. The dashboard shows an error/empty state rather than invented values.
- **A Render Free service is sleeping or losing local files:** this is expected behavior for Free compute; upgrade for production and keep persistent user data in PostgreSQL.

## Current limitations and release checklist

- [x] **Implemented and tested:** application factory, module routes/templates, tenant-scoped authentication/data access, CSRF, password hashing, persistent login throttling, safe error responses, response security headers, local migrations, data validation, and local file-database persistence across app instances.
- [x] **Implemented and tested:** research/question and measurement workflows, CSV import/export, SSRF-hardened bounded technical audits, template/optional LLM content flow, citation planner, report calculations/exports, launch-plan editing, local content-brief generation, and a public static read-only demo that does not read workspace data. Latest local automated test result: 95 passed; the suite reports 151 existing WTForms tuple-choice deprecation warnings.
- [x] **Verified on Render:** commit `4657354` is live on the public Free-plan service. `/`, `/demo`, and `/health` returned HTTP 200; `/dashboard/`, `/research/`, and `/research/questions/new` redirected to sign-in. Live backup/restore and authenticated workflows have not been verified.
- [ ] **Requires credentials if enabling private workflows:** the initial administrator account has not been verified; configure its password only in Render, never in source control or chat. `CONTENT_LLM_API_KEY` is optional and unset; the LLM route has a successful mocked protocol test, not a live provider request.
- [ ] **Requires manual evidence collection:** Google results/AI Overview and ChatGPT, Gemini, Perplexity, Claude, and Copilot measurements; verification of source and citation opportunities; authorized website audit consent; publication and external validation records. The application does not claim that a platform was queried unless an observation is entered.
- [ ] **Not implemented:** automatic AI/search-engine data collection, Google Search Console or indexing verification, email delivery/account recovery, CMS publishing, external schema validation, built-in scheduled worker, automated third-party citation research, account registration, or in-app database backup/restore.

**Release status:** the public Render URL is <https://visibilityforge-ai.onrender.com>; the static read-only demo is at <https://visibilityforge-ai.onrender.com/demo>. The deployed source is commit `4657354` from <https://github.com/Chandru7560/visibilityforge-ai/commit/4657354d57d14ce3f65d1e516af1b69dd6a9145a>. These Free-plan URLs are suitable for a demo, not production data. Private workspace routes remain protected by sign-in, and an administrator account has not been verified. Do not claim authenticated workflows or production backup/restore readiness until they are tested on an appropriate paid plan.
