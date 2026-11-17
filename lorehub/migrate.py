"""State format epochs.

Epoch 3 changed what a record holds (complete bodies instead of trimmed ones, diffs for whole series up
to 1000 patch messages per list, retention that follows the horizon). State written by an earlier
version is kept (its records are still useful, and series with a stored diff are still shown), but
every list's backfill walk starts again from now, so that patch messages and replies are fetched again
with complete bodies and diffs. A record that is fetched again replaces the old one (see
state.merge_records). The walk and the thread completion queue then converge over several runs.
"""

from .state import STATE_EPOCH


def migrate(state, now, log=print):
    """Upgrade the metadata of an older state directory in place. Returns a summary or None if current."""
    meta = state.load_meta()
    old = meta.get("epoch", 0)
    if old >= STATE_EPOCH:
        return None
    ml = meta.setdefault("lists", {})
    meta["epoch"] = STATE_EPOCH
    if not state.list_names() and not any(m.get("bf") for m in ml.values()):
        state.save_meta(meta)  # a fresh deployment has nothing to rebuild
        return None
    reset = 0
    for m in ml.values():
        bf = m.get("bf")
        if bf:
            m["bf"] = {"pg": 0, "done": False}
            reset += 1
        m.pop("cq", None)
    bfm = meta.setdefault("backfill", {})
    bfm["requests"] = 0
    bfm["since"] = now
    bfm["rebuild"] = {"from": old, "at": now, "lists": len(ml)}
    state.save_meta(meta)
    log("state format %d is older than %d: restarting the backfill walk of %d lists (records are kept and replaced when fetched again)"
        % (old, STATE_EPOCH, reset))
    return {"from": old, "to": STATE_EPOCH, "reset": reset}
