"""The vetting rules' decision for a single bio — no network calls: role, the free
rules, being abroad, "the venue is naming its own chef". The same logic as
leadgen.qualify.vetting before any Instagram requests."""
from leadgen.qualify import handlers, passport, roles
from leadgen.qualify import rules as rulebook


def rules(conn, enabled_roles, case):
    """('accept' | 'reject', reason)."""
    bio, full, cat = case.get('bio') or '', case.get('full_name') or '', case.get('category') or ''
    profile = {'full_name': full, 'biography': bio, 'category': cat, 'media_count': 40, 'is_private': False}
    if handlers._venue_naming_staff(case.get('nick') or '', profile):
        return 'reject', 'аккаунт заведения называет своего шефа'
    role, stop = roles.match(enabled_roles, cat, bio)
    ok, reason = rulebook.precheck(profile, role, stop)
    if not ok:
        return 'reject', reason
    where = passport.abroad(conn, bio)
    if where:
        return 'reject', 'вне РФ по шапке: %s' % where
    return 'accept', role['code']
