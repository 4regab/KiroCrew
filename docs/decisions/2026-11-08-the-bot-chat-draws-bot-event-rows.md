# A Bot's chat draws Bot event rows, and no other runtime notice row

Decided by: 4regab (owner of the 4regab/KiroCrew fork; not an upstream maintainer listed in MAINTAINERS.md)
Date: 2026-11-08
Supersedes: 2026-10-17-crewmate-chat-hands-off-to-worker-sessions.md (for the one row kind named below only)

## Decision

A Bot's own chat draws ONE more row kind: the Bot event row (`meta.kind` `bot_event`), a one-line, expandable row for a Bot message or room post the Bot sent or received, or was refused. Every other runtime `notice` row, tool row and injected envelope stays hidden; the rest of the superseded entry, and of the 2026-10-09 and 2026-10-16 entries it builds on, stands.

## Why

- A Bot message and a room wake reach a Bot as hidden turns, so its chat showed nothing between its reply and the person's next message. The person could not tell that the Bot had asked another Bot, what it asked, or what came back.
- Grok Bot draws these events in the Bots' own chats and lets the person open one when there is more to inspect. The row does the same: one quiet line collapsed, the text behind a button.
- The earlier entries keep notices out of the chat because they are the runtime talking to the agent, and the person can do nothing with them. This row is the Bot talking to another Bot on the person's behalf, which is the conversation the person is following.
- The row's text is data from a peer. It is drawn as plain text, never as the person's words or the Bot's own.

## Evidence

- https://github.com/4regab/KiroCrew/pull/5#issuecomment-6719326125 -- the decision, posted from the 4regab account at the owner's explicit request, on the pull request that applies it.
- https://github.com/4regab/KiroCrew/pull/5 -- the change that applies it.
