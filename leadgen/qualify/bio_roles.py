import re

ROLEW = (r'шеф[-\s]?повар|бренд[-\s]?шеф|брэнд[-\s]?шеф|су[-\s]?шеф|шеф[-\s]?кондитер'
         r'|шеф|chef|повар|ресторатор|владел|основат|founder|owner|co-?owner|управляющ|директор')
NEAR = re.compile(r'(' + ROLEW + r')[^@\n]{0,45}@([a-zA-Z0-9._]{3,})', re.I)
BACK = re.compile(r'@([a-zA-Z0-9._]{3,})[^@\n]{0,30}?(' + ROLEW + r')', re.I)
MENTION = re.compile(r'@([a-zA-Z0-9._]{3,})')


def extract_role_mentions(bio):
    """(role, handle) pairs from a venue's bio. Ported from db/tools/venue_staff.py:
    first roles found next to a mention in either direction, then remaining
    mentions tagged as "упомянут" (mentioned), with no role."""
    pairs = [(r, p) for r, p in NEAR.findall(bio or '')]
    pairs += [(r, p) for p, r in BACK.findall(bio or '')]
    known = {p for _, p in pairs}
    for m in MENTION.findall(bio or ''):
        if m not in known:
            pairs.append(('упомянут', m))
    return pairs
