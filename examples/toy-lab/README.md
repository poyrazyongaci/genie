# Toy lab

The whole lab loop in a minute, with no model and no tokens.

```bash
sh examples/toy-lab/run.sh
```

It copies this folder to a temp project. An entry is a file with one number and
the scorer prints it. Lower is better. You will see:

| Step | What to notice |
|---|---|
| `wish`, `next` | the stage comes from files, and one job is printed |
| `golden`, `order`, `--noise`, `freeze` | the scorer earns trust before anything is built |
| a win, `keep` | the number decides, and a keep needs evidence |
| a loss | `keep` refuses it |
| a change to the scorer | the next score is refused |
| a gate | the queue an agent cannot answer |
