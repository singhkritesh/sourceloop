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

The login page is a role picker: click **Buyer** to start (there is no password in the demo). Use the sign-out icon (top right) to switch role.
Password sign-in is still behind "Sign in with a password instead", and `DEMO_ROLE_LOGIN=false` in `.env` turns the picker off. It is never active when `APP_ENV=prod`.

| Role | What it is for |
|---|---|
| **Buyer** (two organisations) | Describes the need and makes every buyer-side decision |
| **Supplier** (two organisations) | Sees the requests it was invited to; its own agent quotes and negotiates within its own limits |
| **SBN admin** | Platform operator: sets platform rules in Studio and Settings, mediates, sees everything |
| **Organization admin** | Manages one organisation's people and agent settings. Never approves anything |

## 3. Your first run

1. On **Projects**, click **New sourcing project**, give it a name and describe what you need in your own words, for example
   *"We need 500 PCB assemblies certified IATF16949 and RoHS, delivered within 8 weeks. Avoid suppliers in Turkey. Target price around 95 per unit."*
2. A small local AI model reads your text and builds the request from it (product, quantity, certificates, countries to avoid, lead time, target price). If it cannot place the request it says so and asks you to rephrase.
3. The agents then run on their own: they find and screen suppliers, request quotes and negotiate. In a live run each agent's next move (accept, counter, walk) is chosen by the AI model from a menu of moves the software has already checked against that agent's own limits, and every turn is checked for fair conduct.
4. You are asked only when a person is really needed: a **flagged supplier**, the **award** (who wins), a **changed protected contract clause** (only if one is touched) and, at the end, **bank details and payment release** in one decision. Click **Review and decide**, read the evidence and approve.
5. When it says **Deal reached**, scroll down for the suppliers, prices, contract and the record of every step.

A run takes a minute or two on a laptop. The model is loaded only while it is working and is unloaded straight afterwards.

## 4. See the other side

Sign out and pick **Supplier** to see the same request from the supplier's side. A supplier sees only its own conversation and never the buyer's limits.

## Something wrong?

| Symptom | Try |
|---|---|
| Page will not load | `./doctor.sh`, then `./start.sh` |
| "Cannot reach the server" | Open Docker Desktop and wait until it says it is running |
| Run seems stuck | It is likely waiting for you under **Approvals**, or the model is still working (give it a minute) |
| "I could not tell what to buy" | Say the product (this demo's registry covers PCB assemblies, connectors, metal castings, precision machining), how many, and any certificates or countries to avoid |
| Want a clean start | `./stop.sh && docker compose down -v && rm data/demo-creds.txt && ./start.sh` (deletes all projects) |

## See how the agents performed

Sign in as **SBN admin**, open a finished project and click **Agent performance**. It shows, for each agent, how many choices the AI model made, how many fell back to the plain rule because the model was unsure, how often it disagreed with the rule, how sure it was and how long it took.
