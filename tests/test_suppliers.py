"""Дилеры по Востоку из 2ГИС (13.10.2026): компании-поставщики посуды и оборудования для общепита по
городам отдела НСК → Instagram с сайта компании → обычная проверка по правилам роли «дилер»."""
from leadgen.sources import suppliers


class FakeGis:
    def __init__(self, pages, sites):
        self.pages, self.sites, self.calls, self.site_calls = pages, sites, [], []

    def search(self, slug, query, page=1):
        self.calls.append((slug, query, page))
        return self.pages.get((slug, query, page), ([], 'ok'))

    def site_instagram(self, slug, gis_id, name, strict=True):
        self.site_calls.append(gis_id)
        return self.sites.get(gis_id, (None, 'no_instagram'))


def company(gid, name, rubrics):
    return {'gis_id': gid, 'name': name, 'kind': 'компания', 'rubrics': rubrics, 'city': 'Новосибирск'}


def queued(stores):
    return [r['payload'] for r in stores.archive.execute(
        "SELECT payload FROM work_queue WHERE task_type = 'profile_info' ORDER BY id").fetchall()]


def test_supplier_instagram_goes_to_vetting_as_dealer_candidate(stores):
    q = suppliers.QUERIES[0]
    gis = FakeGis({('novosibirsk', q, 1): ([company('1', 'Тохорека', ['Оборудование для общепита']),
                                            company('2', 'Гриль Weber', ['Мангалы, коптильни, тандыры'])], 'ok')},
                  {'1': ('tohoreca_nsk', 'ok')})
    out = suppliers.run(stores, gis, cities=[('novosibirsk', 'Новосибирск')], queries=[q], pages=1)
    assert out['instagram'] == 1
    assert queued(stores) == [{'nick': 'tohoreca_nsk', 'venue': '', 'city': 'Новосибирск', 'source': suppliers.SOURCE}]
    assert gis.site_calls == ['1']                      # не поставщик — сайт не смотрим


def test_seen_companies_are_not_checked_again(stores):
    q = suppliers.QUERIES[0]
    gis = FakeGis({('novosibirsk', q, 1): ([company('1', 'Тохорека', ['Посуда'])], 'ok')}, {'1': (None, 'no_site')})
    suppliers.run(stores, gis, cities=[('novosibirsk', 'Новосибирск')], queries=[q], pages=1)
    suppliers.run(stores, gis, cities=[('novosibirsk', 'Новосибирск')], queries=[q], pages=1)
    assert gis.site_calls == ['1']


def test_only_east_cities_by_default():
    deps = {c for _, c in suppliers.east_cities()}
    assert 'Новосибирск' in deps and 'Москва' not in deps and 'Санкт-Петербург' not in deps


def test_supplier_site_with_one_instagram_link_is_taken_even_if_name_differs():
    """«Торговые технологии» → @tt_horeca: у поставщика на сайте — его собственный аккаунт; строгое совпадение
    имени нужно ресторанам (на сайте группы — аккаунт группы), не поставщикам."""
    from leadgen.sources import gis
    assert gis.pick_instagram(['tt_horeca'], 'Торговые технологии', strict=True) == (None, 'no_match')
    assert gis.pick_instagram(['tt_horeca'], 'Торговые технологии', strict=False) == ('tt_horeca', 'ok')
    assert gis.pick_instagram(['tt_horeca', 'other_brand'], 'Торговые технологии', strict=False) == (None, 'no_match')
    assert gis.pick_instagram(['tohoreca', 'x_y'], 'Tohoreca', strict=False) == ('tohoreca', 'ok')


def test_site_files_are_not_instagram_accounts():
    """13.10.2026: ссылка instagram.com/favicon.ico на сайте поставщика ушла в отбор как ник."""
    from leadgen.sources import gis
    html = ('<a href="https://instagram.com/favicon.ico">i</a><a href="https://www.instagram.com/static/logo.png">l</a>'
            '<a href="https://www.instagram.com/tohoreca/">ig</a>')
    assert gis.instagram_links(html) == ['tohoreca']
