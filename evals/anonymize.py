"""Anonymizing bios for the public part of the eval set: nicks, phone numbers, links,
emails get stripped; role and city words stay — the check itself relies on them."""
import re

LINK = re.compile(r'https?://\S+|\b(?:t\.me|wa\.me|vk\.com|taplink\.cc|linktr\.ee)/\S+', re.I)
EMAIL = re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.]+\b')
PHONE = re.compile(r'(?:\+?\d[\s()\-]*){7,}')
HANDLE = re.compile(r'@[A-Za-z0-9_](?:[A-Za-z0-9_.]*[A-Za-z0-9_])?')


def bio(text):
    text = LINK.sub('[ссылка]', text or '')
    text = EMAIL.sub('[почта]', text)
    aliases = {}

    def alias(m):
        return aliases.setdefault(m.group(0).lower(), '@acc%d' % (len(aliases) + 1))
    text = HANDLE.sub(alias, text)
    return PHONE.sub('[телефон]', text).strip()
