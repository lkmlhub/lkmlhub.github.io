"""The reply shard of a thread: what the browser needs to prepare reply e-mails, loaded only by the review dialog and
the Reply buttons, never by a normal page view.

    data/reply/<list>/<n>.json.gz
    {"v": 1, "home": list, "n": number, "addr": 1 when sender addresses are published, 0 otherwise,
     "lists": {list name: posting address},
     "m": [{"i": Message-ID, "p": parent Message-ID, "n": author name, "h": author hash, "t": time, "s": subject}, ...]
          in the order of thread_order (opening message, patches, replies), same as the bodies file,
     "who": {author hash: {"n": name, "a": sender address}}}   (empty when addresses are not published)

This file and state/addrs/<list>.json.gz are the only places that hold e-mail addresses (Message-IDs may contain them
by nature). Pure function: no I/O.
"""

from urllib.parse import unquote


def reply_shard(e, order, amaps, with_addrs=True):
    """Shard of entity `e`. `order` is the message list of thread_order(e), `amaps` maps list name to its stored
    address map ({"post": address, "a": {author hash: address}})."""
    lists = {}
    for name in sorted(e.lists):
        post = (amaps.get(name) or {}).get("post", "")
        if post:
            lists[name] = post
    msgs, who = [], {}
    for m in order:
        item = {"i": unquote(m.mid), "n": m.a, "h": m.h, "t": m.t, "s": m.s}
        if m.p:
            item["p"] = unquote(m.p)
        msgs.append(item)
        if with_addrs and m.h and m.h not in who:
            for name in sorted(e.lists):
                addr = (amaps.get(name) or {}).get("a", {}).get(m.h)
                if addr:
                    who[m.h] = {"n": m.a, "a": addr}
                    break
    return {"v": 1, "home": e.home, "n": e.n, "addr": 1 if with_addrs else 0, "lists": lists, "m": msgs, "who": who}
