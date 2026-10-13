"""Re-walk venues: header (pk, chefs from the header) and subscriptions, REWALK_PAGES pages deep.

    deploy/local/run.sh leadgen.sources.rewalk            # label — current month, e.g. 2026-09
    deploy/local/run.sh leadgen.sources.rewalk 2026-10

Needed once new venues run out: venues walked earlier this month get new chefs over time, while
the earlier pass only looked at the first page of subscriptions. Already-known handles are
deduped, so only new people go into the queue. One label — one pass per venue; runs at lower
priority than fresh work."""
import sys
from datetime import date

from leadgen.core import db, queue as wq
from leadgen.qualify import handlers

TARGET_LEVELS = ('PREMIUM', 'UPPER-MIDDLE')


def enqueue(stores, label, levels=TARGET_LEVELS, pages=None):
    done = {r['ig'] for r in stores.archive.execute(
        "SELECT payload->>'ig_account' AS ig FROM work_queue "
        "WHERE task_type = 'venue_header' AND payload->>'rewalk' = %s", (label,)).fetchall()}
    rows = stores.main.execute(
        "SELECT name, city, ig_account, pk FROM venues WHERE level = ANY(%s) "
        "AND coalesce(ig_account, '') <> '' ORDER BY level = 'PREMIUM' DESC, id",
        (list(levels),)).fetchall()
    queued = 0
    for r in rows:
        ig = r['ig_account'].strip().lstrip('@').lower()
        if ig in done:
            continue
        payload = {'venue_name': r['name'], 'city': r['city'] or '', 'ig_account': ig, 'rewalk': label}
        if r['pk']:
            payload['pk'] = str(r['pk'])
        if pages:
            payload['pages'] = pages
        wq.enqueue(stores.archive, 'venue_header', payload, priority=handlers.REWALK_PRIORITY)
        done.add(ig)
        queued += 1
    return queued


if __name__ == '__main__':
    label = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime('%Y-%m')
    levels = tuple(sys.argv[2].split(',')) if len(sys.argv) > 2 else TARGET_LEVELS
    stores = db.open_stores()
    print('повторный обход %s (%s): поставлено заведений %d' % (label, ', '.join(levels), enqueue(stores, label, levels)))
