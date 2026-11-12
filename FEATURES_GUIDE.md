# Dynasty FC — Integrated System: what was built and how to test it

## 1. Audit of the project before this work

| Requested feature | What existed | Status before |
|---|---|---|
| Document scanning & automation | `/api/documents` stored a title + file path; `documents.html` was a static page with no route | **Missing** – no extraction, classification, validation or routing. Uploaded files were publicly downloadable from `/uploads/…` |
| Finance import + accounting | Finance panel listed rows from the `vw_finance_dashboard` view | **Missing** – no import, validation, duplicates, approval or accounting link. `/api/finance` was readable by anyone |
| ML match predictions | "Predictions" = fans guessing scores | **Missing** – no model |
| AI chatbot | — | **Missing** |
| Management dashboard | Players, matches, messages, inventory live; finance view | **Partial** – no documents/finance workflow/ML/audit; no way to record match results (API existed, no button) |
| System-wide integration | — | **Missing** – modules did not talk to each other |
| Public website | Hard-coded home counters (44 players, 4 teams, 8+ years), different nav on every page | **Partial** – no fixtures/results page |

Security problems found and fixed on the way:
* `/api/finance`, `/api/inventory`, `/api/donors`, `/api/attendance` were open to the internet → now staff only.
* `/api/staff` exposed staff emails/phones publicly → contact fields hidden from non-staff.
* Dashboard inserted contact-form messages, volunteer applications and testimonials into the page without escaping → a visitor could run script in an admin's session (stored XSS). Now escaped; "View" buttons look records up by ID.
* New documents are stored in a private folder and only served to users allowed to see that document type.

## 2. What was built

### Document Centre (`backend/documents.py`, Dashboard → Documents)
1. **Upload** PDF, photos (JPG/PNG/WEBP/TIFF), Word, Excel or CSV — up to 20 at once.
2. **Extract** text: PDF text layer; OCR (Tesseract) for scans and photos; DOCX paragraphs and tables; spreadsheets.
3. **Classify** into invoice, receipt, bank statement, player registration, medical certificate, consent form, contract — keyword rules plus a classifier that **learns from documents staff approve** (corrections improve it).
4. **Extract fields** per type (supplier, invoice no., dates, subtotal/VAT/total; player name, DOB, MYSAFA ID, guardian contacts; fit-to-play; statement lines …).
5. **Validate**: required fields, real dates, nothing in the future, VAT arithmetic, SA ID checksum (only the last 4 digits are stored), age plausibility, expired certificates, **duplicate file** and **duplicate supplier + invoice number**.
6. **Review queue**: staff see the original next to the extracted fields, fix anything highlighted, then approve or reject.
7. **Routing on approval** (processed once, used everywhere):
   * invoice / receipt → approved expense in the finance ledger → ERPNext
   * bank statement → every line into a finance import for checking
   * registration → creates the player (or fills missing details on the existing one) + coach reminder
   * medical certificate → linked to the player, **injured / fit status updated**, coaches notified
   * consent form → linked to the player; contract → renewal reminder
8. **Outstanding documents**: which players are missing registration, consent or a valid medical certificate.

Access: finance documents need finance rights to view/approve; medical documents need medical rights (coaches, system admin).

### Finance (`backend/finance.py`, Dashboard → Finance)
* **Import** bank exports or spreadsheets (CSV/XLSX). Columns are detected automatically (Date, Description, Amount or Debit/Credit, Reference), even with summary lines above the header.
* **Validation**: missing/invalid dates and amounts, future dates, zero amounts, unusually large amounts.
* **Duplicates**: exact (same date/amount/description already in the ledger, already in this file, or waiting in another import) and *likely* (same amount within 3 days with similar wording — e.g. a bank line for a PayFast donation already recorded).
* **Categories** suggested by rules and by what staff approved before.
* **Approval**: clean lines in one click, flagged/duplicate lines individually; lines can be edited and re-checked.
* **Ledger** of approved transactions from imports, documents, manual entries and **paid donations** (pulled in automatically from your existing `donations` table, once each).
* **Budgets** per category with over-budget alerts; 6-month chart; spending by category; CSV export.
* **Open-source accounting: ERPNext.** Each approved transaction is posted as a balanced Journal Entry (bank account vs. income/expense account) through ERPNext's REST API, as a draft by default so your accountant submits it. Retries never create duplicates (each entry carries reference `DFC-<id>`). Failures are shown per transaction with ERPNext's message.
* The separate **Financial Report deliverable was not generated or changed** (waiting for the agreed trigger word).

### Match prediction model (`backend/predictor.py`, Dashboard → Match Intelligence, public `/fixtures`)
* **Training data**: 90,036 real results (2010–2026, 40+ leagues) from openfootball — public domain (CC0) — in `backend/data/historical_matches.csv.gz`, plus Dynasty FC's own recorded results, plus any local history staff import (CSV).
* **Features**, computed only from matches *before* each game: Elo-style rating difference with home advantage, points per game (last 5), goals scored/conceded (last 10), home form at home, away form away.
* **Models compared** on a chronological hold-out (latest 15% of matches, never seen in training): multinomial logistic regression vs. gradient boosting vs. a baseline. Current result: **51% accuracy, log loss 1.009** vs. baseline 45% / 1.065; well calibrated (when it says 65% it is right ~65% of the time). The better model is selected automatically and stored in the database.
* **Dynasty fixtures** get win/draw/loss probabilities, team form, goals per game and the inputs the model used. When a team has fewer than 3 recorded matches the prediction is clearly labelled **"insufficient history"** — no fake confidence.
* New fixtures are predicted immediately; **recording a result grades the prediction** and re-predicts the remaining fixtures. Retrain from the dashboard in ~5 seconds; the model also trains itself on first start.

### Assistant (`backend/assistant.py`, chat button on the dashboard and public pages)
* Answers only from database records and approved documents: player counts, a named player's details/rating/outstanding documents, injuries, top/most-improved players, fixtures with predictions, results, monthly income/expenses, latest transactions, budgets, donations, latest invoice, document queue, outstanding documents, model accuracy, club info, and **document search** ("what did the doctor say about the ankle injury?").
* **Permissions**: visitors and players get public information only; coaches get players/medical documents but not finance; club administrators get finance but not medical documents; system administrators get everything. Refusals say why. If something isn't in the records it says so — it never invents an answer.
* Every question is logged; finance questions are audited.
* **Optional**: set `ANTHROPIC_API_KEY` to have Claude phrase answers naturally. It receives only the facts already retrieved for that user and is instructed to use nothing else.

### Dashboard (`frontend/templates/dashboard.html`, `frontend/js/modules.js`, `frontend/css/modules.css`)
* Home: live tiles for net cash this month, documents waiting, Dynasty win chance for the next match, model accuracy and recent form — all from `/api/overview`.
* New panels: **Documents**, **Finance** (new workflow above your original monthly summary), **Match Intelligence**, **Audit & Automation**.
* **Record result** button on Matches; **notification bell**; deep links such as `/dashboard#documents/12`, `#finance/batch/3`, `#player/5`.
* "Predictions" renamed **Fan Predictions** (unchanged otherwise).

### Integration (`backend/core.py`, `backend/automation.py`, `backend/integrations.py`)
* One permission map for every module, an event bus (handlers run after the database commit), notifications, and an **audit trail** of every upload, approval, sync, import, model training and result.
* Rules: document approved → finance/players; transaction approved → ERPNext + budget check; paid donation → ledger; fixture created → prediction; result recorded → grading + form update + public site; new player → assessment reminder; dashboard load → expiring medical certificates and fixtures missing a result.

### Public website
* New **Fixtures & Results** page (`/fixtures`): next-match countdown with model probabilities, upcoming fixtures by team, latest results, team form, and an honest note on how accurate the model is.
* **One consistent navigation** on every page (Fixtures added).
* Home counters are **live** from the database (players, teams, years running).
* The assistant is available on public pages (public information only).
* Original page designs, heroes and the 3D effects layer are unchanged.

## 3. Files

**New**: `backend/core.py`, `backend/documents.py`, `backend/finance.py`, `backend/predictor.py`, `backend/assistant.py`, `backend/automation.py`, `backend/integrations.py`, `backend/data/historical_matches.csv.gz`, `frontend/js/modules.js`, `frontend/js/assistant-widget.js`, `frontend/css/modules.css`, `frontend/templates/fixtures.html`, `tools/local_demo/*`, `Dockerfile`, `.gitignore`, `.dockerignore`, this guide.

**Changed**: `backend/app.py` (loads the modules; result/fixture events; staff contact hidden publicly; `/fixtures` route), `frontend/templates/dashboard.html` (panels, nav, bell, record result, XSS fix), the public templates (nav, chat, live counters on `index.html`), `requirements.txt`, `Procfile` and `railway.toml` (`--timeout 120` for OCR).

**Database**: new tables are created automatically on start-up (`club_documents`, `player_documents`, `finance_*`, `ml_*`, `assistant_log`, `audit_log`, `notifications`). Existing tables are not altered.

## 4. Test it on your computer

### A. Without your database (local demo — works even while Layerbase is unreachable)
```bash
cd ~/Documents/PROJECTS_2026/Dynasty_Intergrated_system
source venv/bin/activate
pip install -r requirements.txt
brew install tesseract                # optional: needed to read scans/photos
python tools/local_demo/selftest.py   # 54 automatic checks, prints PASS/FAIL
python tools/local_demo/run_demo.py   # open http://127.0.0.1:5001
```
The demo uses its own SQLite file (`tools/local_demo/demo.db`) with a few practice fixtures marked "(demo)". Your real database is never touched.

Then try:
1. `/staff-login` → `admin@dynastyfc.com` / `admin123`.
2. **Documents** → drag in the files from `tools/local_demo/samples/` → open one → check the fields → **Approve & send** → see it appear in Finance / Players.
3. Upload `invoice_sportsmans.pdf` again → it is flagged as a duplicate.
4. **Finance → Import bank / Excel** → `fnb_export.csv` → review flagged lines → **Approve clean lines**. Import `club_ledger.xlsx` → the overlapping line is marked as already in the ledger.
5. **Match Intelligence** → model scores and fixture probabilities → **Matches → Record result** → the prediction is graded.
6. Chat button → "How much have we spent this month?", "Who is injured?", "What did the doctor say about the ankle injury?" (after approving the medical certificate). Log out and ask again → finance is refused.
7. Public `/fixtures` page and the live home counters.

### B. With your real database
`python backend/app.py` as before (fix `DB_HOST`/network first). On start-up you should see `[integrations] … ready` for core, documents, finance, predictor, assistant and automation, and `[ML] trained …` once.

## 5. Deploying to Render
1. Copy the files into your project (`rsync -av` as before — it never deletes your images) and push to GitHub.
2. Render rebuilds automatically. In **Logs** look for the `[integrations]` and `[ML]` lines.
3. **OCR on Render**: the standard Python runtime has no Tesseract, so scans/photos are marked "needs manual entry" (digital PDFs, Word and Excel still work). To enable OCR, switch the service to the included **Dockerfile** (Render → Settings → Runtime: Docker).
4. **Document files** live in `private_documents/`. Render's free disk is wiped on each deploy (extracted data stays in the database). Add a Render persistent disk and set `DOC_STORAGE_DIR` to its path to keep the original files.

### Environment variables
| Variable | Purpose |
|---|---|
| `ERPNEXT_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET` | Your ERPNext site and an API key/secret (ERPNext → User → API Access) |
| `ERPNEXT_COMPANY` | Company name in ERPNext |
| `ERPNEXT_BANK_ACCOUNT`, `ERPNEXT_INCOME_ACCOUNT`, `ERPNEXT_EXPENSE_ACCOUNT` | Account names, e.g. `FNB Cheque - DFC` |
| `ERPNEXT_ACCOUNT_MAP` | Optional JSON mapping categories to accounts, e.g. `{"Transport":"Travel Expenses - DFC"}` |
| `ERPNEXT_SUBMIT` | `1` to submit entries immediately (default: drafts) |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | Optional AI phrasing for the assistant |
| `DOC_STORAGE_DIR` | Where original documents are stored |
| `ML_AUTOTRAIN` | `false` to stop automatic first-time training |
| `PAYFAST_MERCHANT_ID`, `PAYFAST_MERCHANT_KEY`, `PAYFAST_PASSPHRASE` | Online donations (PayFast dashboard → Settings → Integration). Without them the donate page says card payments aren't switched on |
| `PAYFAST_SANDBOX` | `true` while testing with sandbox.payfast.co.za |
| `SITE_URL` | `https://dynasty-fc-webdev.onrender.com` – PayFast sends the payment confirmation here |
| `ADMIN_SEED_PASSWORD` | Password for the first admin account when the database is empty (change `admin123`!) |

ERPNext itself must be running somewhere (Frappe Cloud has a free trial; or self-host). Until it is configured, approved transactions are kept and marked "not configured", and **Sync now** sends them once it is.

## 6. Honest limitations
* Predictions for Dynasty fixtures are only as good as the club's recorded results. With few results the page says so. Recording every result (or importing past local league results) is what makes them better.
* The model is trained mostly on professional league data; it learns how ratings and form translate into outcomes, then applies that to Dynasty's own record.
* OCR accuracy depends on scan quality; anything uncertain stays in the review queue for a person to check.
* The ERPNext connector was tested against a mock server that enforces ERPNext's rules (token auth, balanced entries, reference fields); test it against your own ERPNext with **Test** before relying on it.


---

# Version 4 — roadmap gaps closed (Phases 1, 4, 5, 6)

## What was missing and what was built
| Roadmap item | Before v4 | Now |
|---|---|---|
| Secure staff login (Ph 1) | Passwords stored as unsalted SHA-256; no way to change `admin123` | Salted PBKDF2-SHA256 (600 000 rounds). Old hashes still log in and are upgraded silently on the next login. Logins and failed logins go in the audit trail. A yellow banner warns anyone using a weak/default password, and the **key icon** (bottom-left) changes it with a password policy |
| Role-based permissions (Ph 1) | Coaches could not log in at all; no way to create staff logins | **User Accounts** panel (System Administrator only): create logins with a one-time temporary password, change roles, disable accounts, reset passwords. Can't demote yourself or remove the last administrator. Coaches get a reduced dashboard (squads, fixtures, results, development, documents incl. medical, tasks, analytics) — finance, messages, staff, inventory and accounts are hidden *and* blocked by the server |
| Complete management (Ph 1) | Fixtures, staff and inventory could only be added/deleted; the inventory "Edit" button was an `alert()` | **Edit** on every fixture (coaches too), staff member and inventory item, plus inventory **Delete**, all validated on the server (team can't play itself, scores 0–50, valid email, quantity ≥ 0). Editing a fixture re-runs its prediction; correcting a score re-grades the model. Adding a player now triggers the same automation as a registration form |
| pandas (Ph 4) | Training data loaded with plain Python | `prepare_public_history()` cleans the 90 036 historical results with pandas (dates, numbers, impossible scores, self-matches, future dates, duplicates) and the cleaning report is stored with the model: 89 652 kept, 384 dropped |
| Follow-up tasks (Ph 5) | Notifications only | **Tasks** panel. Created automatically, closed automatically: document needs review → closed when approved/rejected; flagged bank lines → closed when the batch is finished; new player → baseline assessment for coaches, closed when assessed; "not fit" medical certificate → injury follow-up; past fixture without a score → "record the result"; contract stored → renewal reminder 30 days before it ends. Staff can add their own; overdue tasks are highlighted; dashboard tile + sidebar count; the assistant answers "what tasks are open?" |
| Analytics (Ph 6) | None | **Analytics** panel: results per month, per-squad table, squad rating trend, attendance, documents per week and turnaround, approved money in/out, the model's running accuracy on Dynasty matches, assistant usage. Hover any chart for exact numbers, or open "Show as table". Everything is computed from the database when you open it; sections you aren't allowed to see are left out |
| Team profiles (Ph 6) | Player cards only | Each squad on `/teams` shows its record, last five results, goals, clean sheets and next match, linking to the Match Centre. No new player data is made public |
| Broken links (Ph 6) | `/donate` linked from the site but returned **404**, and the donate button called an API that didn't exist | `/donate` works. PayFast integration (`backend/payments.py`): a pending donation is recorded, the page is sent to PayFast with a signed request, and the donation is only marked **paid** after PayFast's confirmation passes the signature check, the amount check and PayFast's validate call. Paid donations flow into Finance automatically |
| Every button works (Ph 6) | — | Automated browser sweep as administrator and as a coach, plus 12 public pages at phone width (390 px): no script errors, no failed API calls, no sideways scrolling |
| Security fixes found on the way | Staff, inventory and finance tables in the dashboard printed database text unescaped (stored XSS) | Escaped |

## Files in v4
**New**: `backend/accounts.py`, `backend/management.py`, `backend/tasks.py`, `backend/analytics.py`, `backend/payments.py`, `frontend/js/v4.js`.
**Changed**: `backend/app.py` (password functions, coach access, player-created event, inventory quantity check), `backend/integrations.py`, `backend/assistant.py` (tasks), `backend/automation.py` (squad stats, task checks), `backend/documents.py` + `backend/finance.py` (task events), `backend/predictor.py` (pandas), `backend/player_dev.py` (assessment event, strong hash for seeded admin), `frontend/templates/dashboard.html`, `frontend/templates/teams.html`, `frontend/js/modules.js`, `frontend/js/pdi.js`, `frontend/css/modules.css`, `requirements.txt` (pandas), `tools/local_demo/*`.
**Database**: one new table, `follow_up_tasks` (created automatically). On MySQL, `users.password_hash` is widened to 255 characters if it is shorter, so the stronger hashes fit — nothing else in your existing tables changes.

## Test v4 locally
```bash
python tools/local_demo/selftest.py      # 54 checks: passwords, coach access, edits, tasks, analytics, pandas, PayFast
python tools/local_demo/run_demo.py --reset
```
1. Log in as `admin@dynastyfc.com` / `admin123` → the yellow banner appears → **Change it now**.
2. **User Accounts → New account** → role Coach → copy the temporary password. Log in with it in a private window: the sidebar has no Finance/Messages/Staff/Inventory, and opening `/api/fin/overview` gives "403".
3. **Documents** → upload a sample → **Tasks** shows "Review document…" → approve it → the task moves to **Done** by itself.
4. **Players → Add player** → as the coach, **Tasks** shows "Baseline assessment" → save an assessment in Player Development → task closes.
5. **Matches → Edit** → change the venue/time; on a played match correct the score → Match Intelligence re-grades it.
6. **Inventory → Edit** (try a negative quantity) and **Delete**.
7. **Analytics** → hover the charts. Then `/teams` for the squad strips and `/donate`.

## Test on Render after deploying
* Log in and check the banner, then change the admin password straight away.
* Create a real coach account and confirm what they can see.
* Donations: add the PayFast variables above with `PAYFAST_SANDBOX=true` and `SITE_URL`, make a sandbox donation, then check **Finance** (a pending "Donation" line) and **Audit** (`donation_paid`). Switch `PAYFAST_SANDBOX` off only when that works.

## Still to do on your side
* Six images referenced by `index.html`/`about.html` were not in the zip you sent (`team news.png`, `Gallery.png`, `upcoming.png`, `our-values.png`, `journey.png`, `updates-hero.jpeg`). If they are in your GitHub repo they will keep working; if not, add them to `frontend/images/`.
* The **Financial Report** deliverable was left untouched.
