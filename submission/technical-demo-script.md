# Technical demonstration recording script

**Maximum:** 8 minutes. **Status:** Not recorded; no video URL is available.

## Before recording

- Use the actual repository and deployed app only after their URLs are verified.
- Do not display `.env`, database credentials, tokens, private data, or candidate-only records.
- Prepare an isolated test account/database and a harmless input that contains no personal data.
- The project records measurements manually; do not describe it as an automated search/AI crawler.

## Suggested timing

1. **0:00–1:00 — Architecture:** show the repository, README, Flask app factory, database/migrations, routes/services, and tests.
2. **1:00–2:00 — Setup/run:** follow the README in a clean environment; show migration and the documented start command. Do not claim a fresh-environment run until performed.
3. **2:00–3:00 — New input:** import or create one legitimate, sourced research question and show its persisted record.
4. **3:00–4:00 — Duplicate behavior:** submit the same question again and show the actual validation/error result. The application rejects duplicate question imports; measurements are append-only.
5. **4:00–5:00 — Refresh/export:** demonstrate a supported report refresh or export. Do not call a report refresh a tracker rerun; the app does not automatically refresh external search results.
6. **5:00–6:00 — Failure/recovery:** reproduce a safe, simple validation/provider-unavailable case and show the surfaced error and recovery. Avoid disabling production services.
7. **6:00–7:00 — Tests:** run `python -m pytest -q`; show the real exit status, count, runtime, and warnings.
8. **7:00–8:00 — Limits:** explain manual evidence capture, lack of live external integrations, and any deployment or data gaps.

Do not claim that a video has been recorded or uploaded until the real file is available at its actual URL.
