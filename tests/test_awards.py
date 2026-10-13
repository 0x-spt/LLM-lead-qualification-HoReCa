from leadgen.sources import awards
from leadgen.core import contacts


PAGE = '''<a href="https://www.instagram.com/wheretoeat.ru/">премия</a>
<a href="https://instagram.com/ember.rest">Agutin</a> <a href="https://www.instagram.com/tundra.vl/">Cantina</a>
<a href="https://www.instagram.com/known_chef/">x</a>'''


def test_pages_cover_regions_and_years():
    pages = awards.pages()
    assert 'https://moscow.wheretoeat.ru/winners_2025/' in pages
    assert 'https://fareast.wheretoeat.ru/winners_2026/' in pages
    assert len(pages) == len(awards.REGIONS) * len(awards.YEARS)


def test_run_queues_new_accounts_with_award_source(stores):
    stores.main.execute("INSERT INTO venues (name, city, level, ig_account) VALUES ('Cantina', 'Владивосток', 'PREMIUM', 'tundra.vl')")
    contacts.reject(stores, 'known_chef', 'x', 'роль')
    fetched = []

    def fetch(url):
        fetched.append(url)
        return PAGE if 'fareast' in url and '2025' in url else ''
    out = awards.run(stores, fetch)
    assert out == {'pages': len(awards.pages()), 'found': 3, 'added': 1}
    task = stores.archive.execute("SELECT payload FROM work_queue WHERE task_type = 'profile_info'").fetchone()
    assert task['payload'] == {'nick': 'ember.rest', 'venue': '', 'city': '', 'source': 'премия'}
    assert awards.run(stores, fetch)['added'] == 0
