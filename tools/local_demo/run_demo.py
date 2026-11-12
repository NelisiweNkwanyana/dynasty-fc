"""
LOCAL DEMO: run the full Dynasty FC site on your computer with a throwaway
SQLite database (tools/local_demo/demo.db). Your real MySQL database is never
touched. Useful for trying the new features while the live database is
unreachable.

    python tools/local_demo/run_demo.py          # then open http://127.0.0.1:5001
    python tools/local_demo/run_demo.py --reset  # start again from scratch

Log in at /staff-login with admin@dynastyfc.com / admin123.
Sample files to upload are in tools/local_demo/samples/.
The demo fixtures/results it creates are made-up practice data, labelled
"(demo)", and exist only in demo.db.
"""
import os
import sqlite3
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
DB = os.environ.setdefault('DEMO_SQLITE', os.path.join(HERE, 'demo.db'))
os.environ['SESSION_COOKIE_SECURE'] = 'false'
os.environ['FORCE_HTTPS'] = 'false'
os.environ.setdefault('DOC_STORAGE_DIR', os.path.join(HERE, 'demo_documents'))


def build(reset=False):
    if reset and os.path.exists(DB):
        os.remove(DB)
    fresh = not os.path.exists(DB)
    c = sqlite3.connect(DB)
    c.executescript(open(os.path.join(HERE, 'schema.sql')).read())
    if fresh:
        today = date.today()
        opps = ['Soweto Stars (demo)', 'Kliptown United (demo)', 'Orlando Queens (demo)', 'Pimville Rovers (demo)']
        scores = [(3, 1), (1, 2), (2, 2), (4, 0), (0, 1), (2, 1), (3, 3), (1, 0)]
        for tg in ('Dynamights', 'Diamond Divas'):
            for i, (a, b) in enumerate(scores):
                d = today - timedelta(days=7 * (len(scores) - i) + 2)
                o = opps[i % len(opps)]
                home = i % 2 == 0
                c.execute("INSERT INTO team_matches (team_group, age_group, home_team, away_team, match_date, match_time, venue, home_score, away_score, is_completed) VALUES (?,?,?,?,?,?,?,?,?,1)",
                          (tg, 'U15', 'Dynasty FC' if home else o, o if home else 'Dynasty FC', d.isoformat(), '10:00', 'Fiat Grounds' if home else 'Away', a if home else b, b if home else a))
            c.execute("INSERT INTO team_matches (team_group, age_group, home_team, away_team, match_date, match_time, venue, is_completed) VALUES (?,?,?,?,?,?,?,0)",
                      (tg, 'U15', 'Dynasty FC', opps[0] if tg == 'Dynamights' else opps[2], (today + timedelta(days=3 if tg == 'Dynamights' else 4)).isoformat(), '10:00', 'Fiat Grounds'))
        c.execute("INSERT INTO donations (donor_name, amount, payment_status, payment_ref, paid_at) VALUES ('Demo donor', 500, 'paid', 'DEMO-1', ?)", (today.isoformat(),))
        c.execute("INSERT INTO messages (name, email, subject, message, is_read, created_at) VALUES ('Demo parent', 'parent@example.com', 'Trials', 'When are U15 trials?', 0, ?)", (today.isoformat(),))
        print('[demo] created', DB)
    c.commit()
    c.close()


if __name__ == '__main__':
    build(reset='--reset' in sys.argv)
    sys.path.insert(0, os.path.join(HERE, 'shim'))
    sys.path.insert(0, os.path.join(ROOT, 'backend'))
    os.chdir(os.path.join(ROOT, 'backend'))
    from app import app
    print('\n  Dynasty FC LOCAL DEMO running at http://127.0.0.1:' + str(os.getenv('PORT', 5001)) + '  (staff login: admin@dynastyfc.com / admin123)\n')
    app.run(host='127.0.0.1', port=int(os.getenv('PORT', 5001)), debug=False)
