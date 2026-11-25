# Stage 2: earn trust in the scorer

A loop cannot judge itself without a number it can trust. Do these in order.

1. Write the T2 command in `lab/genie.toml`. `{entry}` is the entry path and the
   command prints the score last. List the scorer's files under
   `[scorer] paths` and the solution's under `[solution] paths`.
2. `genie golden add <entry> --score <known>` for each published example, then
   `genie golden`. Each must reproduce.
3. `genie order <worse> <base> <better>`. The scorer must rank them that way.
   Past outside scores tell you which entries were better.
4. `genie score --tier T2 --entry <base> --runs 5 --noise` measures the spread.
5. `genie freeze`. A score is refused from now on if a scorer file changed.

Add T0 and T1 when T2 takes more than about a minute.

## When only the outside score is real

Nothing published can be reproduced. Pin your own scores with
`--source pinned`. A pin proves the scorer is stable, not that it is right, so
the order test then needs 3 or more entries that the outside has ranked. If it
ranks them wrongly, the local number cannot judge. Use it for crashes and
direction, and spend outside attempts as experiments with a written prediction.

## Speeding up, cheapest first

- Cache what the change did not touch
- Stop a run once it cannot catch the base
- Choose the T1 cases that separated past entries, not random ones
- Run cases in parallel, only where the score is not a timing
- Keep data on a fast local disk

Run `genie golden` after any of these.
