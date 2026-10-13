"""Eval-набор (12.10.2026): метрики решений и обезличивание примеров для портфолио."""
from evals import anonymize, metrics


def test_binary_metrics_for_accept_class():
    gold = ['accept', 'accept', 'reject', 'reject', 'accept']
    pred = ['accept', 'reject', 'accept', 'reject', 'accept']
    m = metrics.binary(gold, pred, positive='accept')
    assert (m['tp'], m['fp'], m['fn'], m['tn']) == (2, 1, 1, 1)
    assert m['precision'] == 2 / 3 and m['recall'] == 2 / 3 and round(m['f1'], 3) == 0.667
    assert m['accuracy'] == 0.6 and m['n'] == 5


def test_metrics_on_empty_and_one_sided_sets_do_not_divide_by_zero():
    m = metrics.binary([], [], positive='accept')
    assert m['precision'] is None and m['recall'] is None and m['n'] == 0
    m = metrics.binary(['reject'], ['reject'], positive='accept')
    assert m['precision'] is None and m['recall'] is None and m['accuracy'] == 1.0


def test_confidence_interval_is_reported():
    m = metrics.binary(['accept'] * 90 + ['reject'] * 10, ['accept'] * 100, positive='accept')
    lo, hi = m['precision_ci']
    assert lo < 0.9 < hi and 0.8 < lo


def test_anonymize_removes_handles_names_phones_links():
    bio = ('Шеф-повар @fiorentina_nn и @bar.x | Иван Петров | +7 (906) 822-97-81 '
           'https://t.me/ivan wa.me/79068229781 ivan@mail.ru')
    out = anonymize.bio(bio)
    for secret in ('fiorentina', 'bar.x', '906', '822', 't.me/ivan', 'ivan@mail.ru', '79068229781'):
        assert secret not in out
    assert out.startswith('Шеф-повар @acc1 и @acc2')
    assert anonymize.bio('Chef @a @b @a') == 'Chef @acc1 @acc2 @acc1'   # тот же ник — тот же псевдоним


def test_anonymize_keeps_geo_and_role_words_the_eval_depends_on():
    out = anonymize.bio('Brand-chef @sorrel1.tashkent, Ташкент. Розробка меню для закладів')
    assert 'Brand-chef' in out and 'Ташкент' in out and 'закладів' in out and 'quadro' not in out


def test_gold_takes_latest_human_verdict_and_freezes_grader_before_it(stores):
    from evals import build_gold
    m = stores.main
    m.execute("INSERT INTO contacts (nick, name, bio, status) VALUES ('chef_a', 'Иван Петров', 'Шеф-повар @rest_a', 'выгружен')")
    m.execute("INSERT INTO verdicts (nick, stage, verdict, reason, model, by_human, created_at) VALUES "
              "('chef_a', 'текст', 'reject', 'нет заведения', 'm', false, now() - interval '2 days'), "
              "('chef_a', 'человек', 'reject', 'отклонён заказчиком', 'human', true, now() - interval '1 day'), "
              "('chef_a', 'человек', 'accept', 'берём', 'human', true, now()), "
              "('chef_a', 'текст', 'pass', 'поздний', 'm', false, now() + interval '1 day')")
    rows = build_gold.collect(stores)
    assert len(rows) == 1
    r = rows[0]
    assert (r['label'], r['reason'], r['bio']) == ('accept', 'берём', 'Шеф-повар @rest_a')
    assert r['grader_text'] == 'reject'          # вердикт оценщика — до решения человека, не после
    assert r['overturned'] is False


def test_rules_prediction_offline(stores):
    from leadgen.qualify import geo, roles
    from evals import predict
    roles.ensure_defaults(stores.main)
    geo.upsert_city(stores.main, 'Москва', 'Москва', 'МСК')
    enabled = roles.load_enabled(stores.main)
    case = lambda bio, cat='': {'nick': 'x', 'bio': bio, 'full_name': 'Иван Петров', 'category': cat}
    assert predict.rules(stores.main, enabled, case('Шеф-повар @fiorentina_nn, Москва'))[0] == 'accept'
    assert predict.rules(stores.main, enabled, case('Люблю путешествовать'))[0] == 'reject'
    d, why = predict.rules(stores.main, enabled, case('Brand-chef @sorrel1.tashkent @moscow.syncafe.uz'))
    assert d == 'reject' and 'Ташкент' in why
    d, why = predict.rules(stores.main, enabled, case('сами ловим.сами готовим место, где пахнет морем  бренд-шеф @maple1'))
    assert d == 'reject' and 'заведени' in why
