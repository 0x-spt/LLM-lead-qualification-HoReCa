from leadgen.core import contacts, venues


def test_norm_nick_strips_at_case_and_trailing_dot():
    assert contacts.norm_nick('@Ivan.Chef.') == 'ivan.chef'
    assert contacts.norm_nick('  grove1.chef ') == 'grove1.chef'


def test_unseen_nick_is_not_known(stores):
    assert contacts.is_known(stores, 'novy_nick') is False


def test_nick_in_main_contacts_is_known_case_insensitive(stores):
    stores.main.execute("INSERT INTO contacts (nick, status) VALUES ('grove1.chef', 'выгружен')")
    assert contacts.is_known(stores, '@grove1.chef') is True


def test_rejected_in_archive_is_known(stores):
    contacts.reject(stores, 'sable', 'профиль выглядит дёшево', 'оценка')
    assert contacts.is_known(stores, 'sable') is True


def test_candidate_in_archive_is_known(stores):
    contacts.add_candidate(stores, 'ivan_chef', '42', {'full_name': 'Иван'}, {'venue': 'X'}, 'Агент №2')
    assert contacts.is_known(stores, 'ivan_chef') is True


def test_renamed_account_is_caught_by_pk(stores):
    """Человек сменил ник — pk тот же. Без проверки по pk он придёт повторно."""
    stores.main.execute("INSERT INTO contacts (nick, pk, status) VALUES ('old_nick', '555', 'выгружен')")
    assert contacts.is_known(stores, 'new_nick', pk='555') is True
    assert contacts.is_known(stores, 'new_nick', pk=555) is True


def test_add_candidate_stores_normalized_nick_agent_and_status(stores):
    contacts.add_candidate(stores, '@Ivan_Chef', 42,
                           {'full_name': 'Иван', 'biography': 'Шеф', 'category': 'Шеф-повар',
                            'city_name': 'Омск'},
                           {'venue': 'Fiorentina', 'city': 'Омск', 'role': 'шеф',
                            'source': 'venue_header'}, 'Агент №2')
    row = stores.archive.execute('SELECT * FROM candidates').fetchone()
    assert row['nick'] == 'ivan_chef'
    assert row['pk'] == '42'
    assert row['status'] == 'обогащён'
    assert row['agent'] == 'Агент №2'
    assert row['role_hint'] == 'шеф'
    assert row['venue'] == 'Fiorentina'


def test_add_candidate_twice_keeps_first(stores):
    contacts.add_candidate(stores, 'ivan', '1', {}, {'venue': 'A'}, 'Агент №2')
    contacts.add_candidate(stores, 'ivan', '1', {}, {'venue': 'B'}, 'Агент №2')
    assert stores.archive.execute('SELECT venue FROM candidates').fetchone()['venue'] == 'A'


def test_reject_records_reason_and_stage(stores):
    contacts.reject(stores, 'Someone', 'нет роли в шапке', 'роль', pk=7, source='following')
    row = stores.archive.execute('SELECT * FROM rejected').fetchone()
    assert (row['nick'], row['pk'], row['reason'], row['stage'], row['source']) == \
        ('someone', '7', 'нет роли в шапке', 'роль', 'following')


def test_rejected_never_written_to_main(stores):
    contacts.reject(stores, 'someone', 'нет роли в шапке', 'роль')
    assert stores.main.execute('SELECT COUNT(*) AS c FROM contacts').fetchone()['c'] == 0


def test_cache_profile_truncates_bio_and_upserts(stores):
    contacts.cache_profile(stores, 1, {'username': 'A', 'biography': 'x' * 900})
    contacts.cache_profile(stores, 1, {'username': 'B', 'biography': 'short'})
    row = stores.archive.execute('SELECT * FROM profile_cache').fetchone()
    assert row['nick'] == 'b'
    assert row['bio'] == 'short'
    contacts.cache_profile(stores, 2, {'username': 'C', 'biography': 'y' * 900})
    long_bio = stores.archive.execute("SELECT bio FROM profile_cache WHERE pk = '2'").fetchone()['bio']
    assert len(long_bio) == 500


def test_venue_pk_roundtrip(stores):
    m = stores.main
    m.execute("INSERT INTO venues (name, city, ig_account) VALUES ('Dorian Gray', 'Екатеринбург', 'Dorian_Gray_ekb')")
    assert venues.stored_pk(m, 'dorian_gray_ekb') is None
    venues.store_pk(m, 'dorian_gray_ekb', 123)
    assert venues.stored_pk(m, 'Dorian_Gray_ekb') == '123'


def test_venue_set_account(stores):
    m = stores.main
    m.execute("INSERT INTO venues (name, city) VALUES ('Fiorentina', 'Нижний Новгород')")
    venues.set_account(m, 'Fiorentina', 'Нижний Новгород', 'fiorentina_nn', 9)
    row = m.execute('SELECT ig_account, pk FROM venues').fetchone()
    assert (row['ig_account'], row['pk']) == ('fiorentina_nn', '9')
