"""Transport to Instagram: own accounts (own) or HikerAPI (hiker).

Task handlers know only this contract. Each method returns data and a
status; the status decides what happens next:

    ok          — data was returned
    empty       — the response is empty (for own accounts this is often a
                  silent block; for HikerAPI it's a genuine "not found", see silent_empty_is_block)
    throttled   — 429, the endpoint cools down for 15 minutes
    soft_block  — a silent refusal: HTML instead of JSON, a timeout, a weird status code
    checkpoint  — needs a human: account login, or the HikerAPI key was rejected
    proxy_dead  — the account's proxy isn't responding
    net_error   — a network hiccup along the way (VPN timeout), endpoint cools down for a minute

Methods: search, pk_by_nick, profile_info, following, media_info,
user_feed, highlights_latest, download, close; nick-based lookup without API
endpoints: profile_by_nick (profile page), embed_feed (/embed/ page)."""

OK = 'ok'
EMPTY = 'empty'
THROTTLED = 'throttled'
SOFT_BLOCK = 'soft_block'
CHECKPOINT = 'checkpoint'
PROXY_DEAD = 'proxy_dead'
# a network hiccup along the way (timeout, a VPN response cut short) — not a response from Instagram
NET_ERROR = 'net_error'

FAILURES = frozenset({THROTTLED, SOFT_BLOCK, CHECKPOINT, PROXY_DEAD, NET_ERROR})
