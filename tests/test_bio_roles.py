from leadgen.qualify.bio_roles import extract_role_mentions


def test_extract_role_mentions_near_pattern():
    pairs = extract_role_mentions('Шеф @ivan_chef, добро пожаловать!')
    assert ('Шеф', 'ivan_chef') in pairs


def test_extract_role_mentions_back_pattern():
    pairs = extract_role_mentions('@ivan_chef — шеф-повар ресторана')
    assert any(p == 'ivan_chef' and 'шеф' in r.lower() for r, p in pairs)


def test_extract_role_mentions_unlabeled_mention_gets_upomyanut():
    pairs = extract_role_mentions('Партнёры: @some_partner')
    assert ('упомянут', 'some_partner') in pairs


def test_extract_role_mentions_empty_bio_returns_empty_list():
    assert extract_role_mentions('') == []
    assert extract_role_mentions(None) == []


def test_extract_role_mentions_does_not_duplicate_role_tagged_mention():
    pairs = extract_role_mentions('Шеф @ivan_chef')
    assert pairs.count(('Шеф', 'ivan_chef')) == 1
    assert ('упомянут', 'ivan_chef') not in pairs
