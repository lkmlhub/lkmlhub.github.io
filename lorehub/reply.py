"""The reply shard of a thread: what the browser needs to prepare reply e-mails, loaded only by the review dialog and
the Reply buttons, never by a normal page view.

    data/reply/<list>/<n>.json.gz
    {"v": 1, "home": list, "n": number, "addr": 1 when sender addresses are published, 0 otherwise,
     "lists": {list name: posting address},
     "m": [{"i": Message-ID, "p": parent Message-ID, "n": author name, "h": author hash, "t": time, "s": subject,
            "ah": hash of the real author (only for mail sent through the b4 relay)}, ...]
          in the order of thread_order (opening message, patches, replies), same as the bodies file,
     "who": {author hash: {"n": name, "a": sender address}}}   (empty when addresses are not published)

This file and state/addrs/<list>.json.gz are the only places that hold e-mail addresses (Message-IDs may contain them
by nature). Pure function: no I/O.
"""

import re
from urllib.parse import unquote

RELAY_RE = re.compile(r"^devnull\+[^@\s]+@kernel\.org$", re.I)  # the b4 web endpoint relay: mail to it is discarded


def reply_shard(e, order, amaps, with_addrs=True):
    """Shard of entity `e`. `order` is the message list of thread_order(e), `amaps` maps list name to its stored
    address map ({"post": address, "a": {author hash: address}})."""
    lists = {}
    for name in sorted(e.lists):
        post = (amaps.get(name) or {}).get("post", "")
        if post:
            lists[name] = post
    def address(h):
        for name in sorted(e.lists):
            addr = (amaps.get(name) or {}).get("a", {}).get(h)
            if addr:
                return addr
        return ""

    def relay_author(m, addr):
        """Mail sent through the b4 web endpoint comes from devnull+...@kernel.org, which discards replies; the real author
        is the Signed-off-by of the patch with the same name. Returns (hash, name) of that author, or None."""
        if not addr or not RELAY_RE.match(addr):
            return None
        base = re.sub(r"\s+via\s+B4\s+Relay$", "", m.a, flags=re.I).strip().casefold()
        for t in m.tr or ():
            if t[0] == "signed" and t[1].strip().casefold() == base and t[2] != m.h:
                return t[2], t[1].strip()
        return None

    msgs, who = [], {}
    for m in order:
        item = {"i": unquote(m.mid), "n": m.a, "h": m.h, "t": m.t, "s": m.s}
        if m.p:
            item["p"] = unquote(m.p)
        msgs.append(item)
        if not with_addrs:
            continue
        if m.h and m.h not in who and address(m.h):
            who[m.h] = {"n": m.a, "a": address(m.h)}
        alt = relay_author(m, address(m.h))
        if alt and address(alt[0]):
            item["ah"] = alt[0]
            who.setdefault(alt[0], {"n": alt[1], "a": address(alt[0])})
    return {"v": 1, "home": e.home, "n": e.n, "addr": 1 if with_addrs else 0, "lists": lists, "m": msgs, "who": who}
