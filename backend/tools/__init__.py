"""Everything the models in this system are allowed to reach for themselves.

Split by who holds it, because the two sets are not remotely equivalent:

  `tutor_tools`  — read-only, scoped to one learner. Held by the tutor, which
                   any learner can talk to.
  `admin_tools`  — can rewrite the rules everyone runs under, delete another
                   learner's note, and erase a learner's memory. Held by the
                   operator subagent, reachable only after `admin.is_admin()`
                   has already passed.

Registered through LangChain's `@tool` interface and bound to the model, so the
model decides when to call them. Nothing in this package is invoked by routing.
"""
