# Getting started (no technical background needed)

SourceLoop shows how software agents could buy parts for a company. You say what you need. A buyer agent finds suppliers, checks them, asks for
quotes and negotiates. People make the big calls. Everything uses made-up data, so nothing you do affects a real company.

## 1. Install (about 15 minutes, mostly downloading)

On a MacBook, open Terminal in the project folder and run:

```bash
./install-mac.sh
```

It installs what is missing (Docker Desktop, Ollama, a small 4b AI model) and starts the app. macOS may ask for your password and to approve Docker.
Then check everything with:

```bash
./doctor.sh
```

`doctor.sh` prints `ok` or `FAIL` for each part and tells you what to do about any failure. When it says **All good**, open http://localhost:3010.

Needs 8 GB memory (16 GB is comfortable) and about 10 GB of free disk. Stop the app with `./stop.sh`.

## 2. Sign in

The login page has a "Which account should I use?" note. Use `buyer@sourceloop.test` first. Passwords were generated during install. Show them with:

```bash
cat data/demo-creds.txt
```

## 3. Your first run

1. On **Projects**, click **Run a sample project**.
2. The bar at the top shows the six stages (Need, Discovery, Qualification, Sourcing, Contracting, Onboarding). The card under it says what is happening.
3. When it says **Needs a decision**, click **Review and decide**, read the evidence and approve. A full run needs several people, as in real life: after you confirm the need, the project waits for **Compliance** (`compliance@sourceloop.test`), then **Quality** (`quality@`), you again for the award, **Legal** (`legal@`) for protected clauses and **Finance** (`finance@`) for payment. The project page always says who it is waiting for. Sign out and in as that person (passwords are in `data/demo-creds.txt`), approve, and the run moves on.
4. When it says **Deal reached**, scroll down for suppliers, prices, the contract and the record of every step.

The blue **Demo mode** note means the agents replay recorded answers, so a run is repeatable but does not react to your exact words. To use the local AI model
instead, sign in as `superadmin@sourceloop.test`, open **Settings** and switch to live mode (or install with `./install-mac.sh --live`). Live runs are slower
(about a minute or more) and the model is loaded only while a run is working.

## 4. See the other side

Sign in as `supplier1@sourceloop.test` to see the same project from the supplier's side. A supplier sees only its own conversation and never the buyer's limits.

## 5. Words you will see

The **Help** page in the app has a short glossary (agent, limits, qualification, RFQ, audit record, and more).

## Something wrong?

| Symptom | Try |
|---|---|
| Page will not load | `./doctor.sh`, then `./start.sh` |
| "Cannot reach the server" | Open Docker Desktop and wait until it says it is running |
| Run seems stuck | It is likely waiting for you under **Approvals** |
| Forgot a password | `cat data/demo-creds.txt` |
| Want a clean start | `./stop.sh && docker compose down -v && rm data/demo-creds.txt && ./start.sh` (deletes all projects) |
