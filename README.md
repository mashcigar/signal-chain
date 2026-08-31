# signal chain

A four stage agent chain that researches public companies, scores them against a written rubric,
tries to break its own findings, and then stops and waits for a human.

Built from an empty directory in a single working session, with Claude Code, using the Python
standard library only. No dependencies, no API key, no account. Clone it and run it.

```
python3 chain.py
```

## What it does

```
  research  ->  qualify  ->  verify  ->  digest  ->  [ GATE ]
```

1. **research** reads public marketing and careers pages and records claims. Every claim carries the
   URL it came from, the timestamp it was read at, and the exact characters that produced it. Claims
   with no source are allowed in at this stage, on purpose. See below.
2. **qualify** scores each target against `rubric.md`. The rubric is a file you can read and argue
   with, not a prompt buried in the code.
3. **verify** exists only to break stage 2. It checks that every claim has a source, has a timestamp,
   is not stale, and is still grounded in the stored copy of the page. Anything that fails is dropped,
   and the reason is recorded. Then the score is recomputed from survivors only.
4. **digest** renders a Slack shaped message from what survived.

Then it stops. The digest is written to `outbox/pending/` and nothing is sent. Approval is a separate
program run by a person:

```
python3 approve.py                          # what is waiting
python3 approve.py <run-id> --i-approve     # approve one
```

Approving moves the file and appends a line to an append only audit log. It still does not send.
Wiring a real Slack credential is a deliberate separate act, and it belongs behind this same gate.

## The part worth looking at

Claims persist between runs in `state/claims-ledger.json`, and that is the design decision the whole
thing rests on. Without a ledger, research and verification run in the same breath off the same
bytes and can never disagree. The only thing verification could catch would be something you
planted, which proves nothing.

With a ledger, a claim recorded on Monday gets re-checked against Friday's page. Two different
things can now kill it, and only one of them is a plant.

**One, no source.** The seed file carries hand entered intel, the kind that lives in a CRM because
somebody typed it after a call. It never survives, because it has no URL behind it.

```
  Vanta          kept  6  dropped  1   75/100  pursue  moved -25
       drop  paid_motion  no source url
```

Vanta looked like a 100 until the only thing supporting a 25 point dimension turned out to be a
typed sentence.

**Two, the page moved.** This is the honest one. Verification re-reads the stored source and
confirms the evidence is still present. When a company rewrites a line of homepage copy, the claim
that rested on it dies on the next run and says why:

```
       drop  scale_band   evidence not found in the stored copy of the source
```

Nobody planted that. It fires on its own whenever the world moves, which is what provenance decay
actually looks like. One honest detail about freshness: grounding checks the stored copy of each
page, and the stored copy only changes when you run with `--refresh`. Run refreshed on whatever
cadence you care about; the 30 day staleness cutoff kills old claims either way. In that particular run the dimension survived anyway, because a second claim on
another page still supported it. That is correct behavior and worth watching for: redundant evidence
is the difference between a score that wobbles and a score you can act on.

## Run artifacts

```
  cache/                    stored copies of every page read, with retrieval timestamps
  out/<run-id>/claims.json  every claim, including the ones that were dropped
  out/<run-id>/verification.json   what was kept, what was dropped, and why
  out/audit.jsonl           append only record of human approvals
  outbox/pending/           digests waiting on a person
  outbox/approved/          digests a person released
```

## Options

```
python3 chain.py --refresh    refetch pages instead of using cache
python3 chain.py --offline    cache only, never touch the network
```

## Limits, stated plainly

Overclaiming is the failure mode worth avoiding here, so:

- **The target set is five companies.** This demonstrates a pattern. It is not a production run.
- **The signals are proxies.** A tag manager script on a homepage suggests a paid motion. It does not
  prove a media budget. `rubric.md` says so too.
- **The gate is enforced in this program's runtime.** It is not a managed policy layer that a
  determined operator could not edit. A real deployment puts the boundary somewhere the running
  process has no authority to change.
- **There is no LLM in the loop by default.** The default extraction is literal string matching,
  which makes every claim trivially auditable and makes this run for free. An optional model stage
  exists (below), and it is held to the same standard as the regex: its claims must survive
  verification or they die in the drop log.
- **The seed notes are deliberately fabricated hearsay about real companies.** Nobody said those
  things. They exist so you can watch unsourced intel die in stage 3, and it does, every run.
- **No multi tenancy, no access control.** At five targets on one machine that would be theater.

## Where the model goes, optionally

There is a model seam, and it is deliberately narrow. Set one variable and stage 1 adds a
model-backed observer alongside the matcher:

```
export ANTHROPIC_API_KEY=...     # optional; SIGNAL_MODEL overrides the default model
python3 chain.py --refresh
```

The contract is the part worth reading (`model.py`): the model is one more untrusted researcher.
Every claim it emits must carry a verbatim quote from the page, and verification confirms that
quote against the stored bytes exactly the way it checks a regex claim. A hallucinated quote is
not an exception or a warning. It is a dropped claim with a reason in the log, sitting next to
the dead hearsay. Nothing downstream knows which observer produced a claim, and that is the
design: trust attaches to evidence, never to the author.

Without the key, nothing changes and everything above still runs for free.

## Tests

```
python3 test_chain.py
```

Standard library unittest, no runner to install. The suite covers claim identity, ledger merging,
rubric banding, every drop reason in verification, and the model contract with a mocked API,
including the case where the model invents a quote and stage 3 kills it. CI runs the suite and an
offline chain run on every push.

## License

MIT. See `LICENSE`.

## Why it is shaped this way

Because the interesting problem in agentic go to market is not capability. It is whether the thing
can be trusted to act. Provenance on every claim, an adversarial pass that records its own deletions,
and a gate that actually blocks are the three cheapest things that move a demo toward something an
enterprise could sign off on.
