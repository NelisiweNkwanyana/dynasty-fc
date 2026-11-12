# Dynasty FC — Player Development Intelligence upgrade

## Public pages + logins: original design kept, 3D effects layered on top
All pages keep their own heroes, images, colours and layout. Two files add the effects:
`frontend/css/dynasty-fx.css` and `frontend/js/dynasty-fx.js` (linked with two lines per page).
- **Gallery** — the ball is lifted out of `gallery-hero.png` automatically and replaced with a real-time
  3D ball the boy keeps kicking up off his boot (gold sparks on contact, motion trail). Click the hero for a power kick.
  If the image ever changes and no separate ball can be found, the page simply shows the original photo.
- **Home** — the crest tilts in 3D with the mouse, has a moving gold shine, and a small 3D ball orbits behind and in front of it.
- **Teams** — flickering stadium floodlights and light beams over the existing stadium background.
- **About, Get Involved, Contact, News** — floating light particles (warm embers on Get Involved) and 3D depth on the hero text.
- **Everywhere** — cards tilt with a light glare under the mouse, CTA buttons are magnetic, and a gold scroll-progress bar.
- **Staff login** — your original white card, with 3D footballs floating behind it and gold particles. Logging in plays the dashboard welcome intro.
- Effects switch off automatically for visitors who prefer reduced motion. Add `?fxdebug` to a URL to log what the gallery detection found.

## Dashboard
- **Staff login** — original design restored (see above).
- **Welcome intro** — after logging in, staff see a short greeting with live club facts (players, squad rating, next fixture). Click to skip.
- **Dashboard home** — greeting + live clock, top-performer spotlight, next-fixture countdown, development pulse, count-up stats, grouped sidebar.
- **Player Development Intelligence** (new sidebar item):
  - Player cards with photo, overall rating, position, six attributes (PAC TEC PAS DEF PHY MEN), trend and injury flags
  - Filter by Dynamights / Diamond Divas, search, sort (rating, most improved, goals, attendance, number, name)
  - Squad KPIs and insights: progression chart, most improved, attendance alerts (<75%), players due for assessment, squad profile
  - Player profile drawer: radar chart vs start of season, changes since last assessment, season stats, attendance; Progress tab with per-attribute history and coach notes; New assessment with live preview; Edit details, stats and photo
  - Compare any two players side by side
- **Command palette** — press `Ctrl K` (or `/`) to jump to any page or player.
- Toast notifications replace browser alert pop-ups. Mobile layout gets a top bar and slide-out menu.

## Backend
- `backend/player_dev.py` (new) adds `player_profiles`, `player_season_stats`, `player_assessments` (created automatically on startup) and staff-only APIs under `/api/pdi/...`.
- On startup it creates the admin login **admin@dynastyfc.com / admin123** if that email doesn't exist yet (it never overwrites an existing account). Override with `ADMIN_SEED_EMAIL` / `ADMIN_SEED_PASSWORD`.
- It seeds the 13 U15 boys (Dynamights) and 15 U15 girls (Diamond Divas) with photos from `frontend/images/players/` and **sample** stats/assessments. Set `SEED_U15_PLAYERS=false` to turn this off.

## Fixes
- `/api/players-teams` no longer sends players' date of birth, MYSAFA ID or guardian contact details to visitors who aren't logged-in staff; `/api/players` is now staff-only.
- Add Player now asks for the real MYSAFA ID and date of birth (it previously saved a fake 2005 birth date and fake contact details).
- `showPanel()` no longer crashes when opened from Quick Access cards; duplicate `staffEmail` element ID fixed.

## Before going live
1. Rename the placeholder player names (Edit tab in each profile) and replace sample ratings/stats with real coach assessments.
2. Change the admin password after first login, and confirm guardian consent for player photos.
3. `frontend/images/login-background.mp4` and `contact-hero.mp4` carry iStock / Getty Images watermarks (unlicensed previews) — replace or license them.
