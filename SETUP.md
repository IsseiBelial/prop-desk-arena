# Running the Arena on a spare PC

This guide sets the spare PC up to run the Prop Desk Arena on its own for a whole season: $100 per faction,
two rounds every weekday, on your Claude plan. Budget about 30–45 minutes. Windows comes first; Mac and Linux
differences are in [step 7](#7-mac-or-linux-instead-of-windows).

**What you need**
- The spare PC, plugged in, with internet access
- Your Claude account (Team or Max plan)
- Access to the GitHub repo `IsseiBelial/vllmTesting`

> Everything is paper trading. No real money moves. The PC only needs to reach Anthropic, Yahoo Finance and
> Kalshi.

---

## 1. Install the tools (Windows)

Open **PowerShell**: press Start, type *PowerShell*, and press Enter. Paste each line and press Enter.

```powershell
winget install -e --id Python.Python.3.12
winget install -e --id Git.Git
```

Close PowerShell and open a new one so it sees the new programs. Then install **Claude Code**. The Arena uses it
to sign in with your Claude plan.

```powershell
irm https://claude.ai/install.ps1 | iex
```

Close and reopen PowerShell once more, then check that all three answer with a version number:

```powershell
py --version
git --version
claude --version
```

> If `winget` isn't recognized, install Python from python.org (tick **"Add python.exe to PATH"**) and Git
> from git-scm.com, using the default options.

## 2. Sign in to Claude

```powershell
claude auth login
```

A browser window opens. Sign in with your Claude account and approve. Usage then counts against your plan's
usage limits. You don't need an API key.

## 3. Download the Arena and install its parts

```powershell
cd $HOME
git clone -b ccr-ef4ab90f-8bj1g0 https://github.com/IsseiBelial/vllmTesting.git prop-desk-arena
cd prop-desk-arena
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r prop_desk\requirements.txt
```

- If Git asks you to sign in to GitHub, do so in the browser window it opens.
- Once PR #1 is merged, you can leave out `-b ccr-ef4ab90f-8bj1g0`.
- The last command takes a few minutes.

## 4. Choose your settings

```powershell
copy prop_desk\arena.env.example prop_desk\arena.env
notepad prop_desk\arena.env
```

The defaults already match the plan: four factions with $100 each, 30-day seasons, rounds at 9:45am and 3:30pm
US-Eastern, and Iron Legion on Sonnet while the other three use Opus. Things you might change:

| Setting | Why |
|---|---|
| `PROP_DESK_MODEL_LEGION=claude-sonnet-5-5` | Iron Legion makes almost all the Claude calls, so it runs on Sonnet to halve its cost. Delete the line to put it back on Opus. |
| `PROP_DESK_MODEL=claude-sonnet-5-5` | Puts every faction on Sonnet. Good if you still hit limits. |
| `PROP_DESK_FACTIONS` | Who competes. Remove one to sit it out. |
| `PROP_DESK_SCHEDULE_ET=09:45,12:30,15:30` | Three rounds a day instead of two. Uses more plan usage. |
| `PROP_DESK_RISK_PCT` / `PROP_DESK_MAX_POS_PCT` | How aggressive every faction may be. The defaults are 1% risk per trade and 10% max per stock. |
| `PROP_DESK_ASSETS` | What they may trade. The default is everything: stocks, lively stocks, crypto, currencies and options. Remove any you don't want. |
| `PROP_DESK_OPTIONS_TRADE_PCT` | At $100 most options are vetoed as too expensive, because one contract covers 100 shares. Raising this lets them in sooner, at more risk. |

Save the file and close Notepad.

**Rough plan usage:** with Iron Legion on Sonnet, each round is about $1.20 at API prices, almost all of it Iron
Legion. Two rounds a day over about 22 trading days comes to about $55 a month of usage on your plan (about $95 with
everyone on Opus).

## 5. Run the health check

```powershell
.venv\Scripts\python -m prop_desk.check
```

Every line should say `[ OK ]`, and it should end with **Ready**. In particular:
- **Claude: using your Claude plan login** → step 2 worked.
- **Live stock data: SPY last close $…** → Yahoo Finance is reachable.
- **Lively-stock screener**, **Live crypto**, **Live forex** and **Option chains** → the extra instruments
  are reachable. A `[WARN]` here just means that instrument falls back to a built-in list or a model price.
- **Live Kalshi data** → prediction markets are reachable.

If anything says `[FAIL]`, the line under it tells you what to do. Also see [Troubleshooting](#troubleshooting).

## 6. First launch and a test round

1. In File Explorer, open your user folder → `prop-desk-arena` → `prop_desk`, and double-click
   **`run_arena.bat`**. A black window opens. **Leave it open**; it is the Arena. If it ever crashes, it
   restarts itself after 30 seconds.
2. Open a browser on the PC and go to **http://localhost:8000**.
3. Check the badges at the top right. They should read **Claude plan · claude-…** and
   **data: yahoo · markets: kalshi**. If you see "synthetic", the PC can't reach the market data. Re-run step 5.
4. Press **R** to run a test round and watch the four factions take their turns. It takes about 5 minutes.
5. When you're happy with it, press **X** (Reset) so the test round doesn't count. **Season 1 starts at the
   next scheduled round.**

### Keep it running all season (Windows)

**Stop the PC from sleeping:** Settings → System → Power (or Power & battery) → Screen and sleep → set
**"When plugged in, put my device to sleep after"** to **Never**. The screen may still turn off; that's fine.

**Keep Windows Update from restarting it mid-session:** Settings → Windows Update → Advanced options →
**Active hours** → Manually → 8:00 AM to 6:00 PM.

**Start the Arena automatically when you sign in:**

1. Start → type **Task Scheduler** → open it → **Create Task…** (not "Basic Task").
2. **General** tab: Name it `Prop Desk Arena`. Leave **"Run only when user is logged on"** selected.
3. **Triggers** tab → New… → **Begin the task: At log on** → your user → OK.
4. **Actions** tab → New… → Program/script: **Browse** to `prop_desk\run_arena.bat`. In **Start in**, put
   the `prop-desk-arena` folder path, e.g. `C:\Users\you\prop-desk-arena`. Then OK.
5. **Conditions** tab: untick **"Start the task only if the computer is on AC power"**.
6. **Settings** tab: untick **"Stop the task if it runs longer than 3 days"**. *This one matters: otherwise
   Windows kills the Arena on day 3.*
7. OK. Sign out and back in to test it: the black window should appear on its own.

After a restart, the Arena starts again as soon as you sign in to the PC. Your season, accounts and track
records are saved in `prop_desk\data`, so nothing is lost. Rounds simply don't run while the PC is off.

## 7. Mac or Linux instead of Windows

**Mac:**

| Step | What to do |
|---|---|
| Install tools | Install Python 3.12 from python.org and Git (`xcode-select --install`). Install Claude Code with `curl -fsSL https://claude.ai/install.sh \| bash`. |
| Steps 2–5 | Same as Windows, using `python3.12 -m venv .venv`, `.venv/bin/python …` and `cp` instead of `copy`. |
| Run it | `caffeinate -dims ./prop_desk/run_arena.sh` in Terminal, and leave the window open. `caffeinate` keeps the Mac awake. |

**Linux:**

| Step | What to do |
|---|---|
| Install tools | Same as Mac. Use your distro's Python 3.11+ package if it has one. |
| Run it | To run as a service, create `~/.config/systemd/user/prop-desk.service` (below), then run `systemctl --user enable --now prop-desk` and `loginctl enable-linger $USER`. |

```ini
[Unit]
Description=Prop Desk Arena
[Service]
WorkingDirectory=%h/prop-desk-arena
ExecStart=%h/prop-desk-arena/.venv/bin/python -m prop_desk.server
Restart=always
[Install]
WantedBy=default.target
```

---

## Watching from your phone or laptop (optional)

The dashboard runs on the spare PC itself. To open it from other devices **on your home Wi-Fi**:

1. In `arena.env`, add the line `HOST=0.0.0.0`, then restart the Arena (close the black window and
   double-click the launcher).
2. If Windows asks whether to allow Python on networks, allow **Private networks** only.
3. On the PC, run `ipconfig` and note the **IPv4 Address**, e.g. 192.168.1.50. Open `http://192.168.1.50:8000`
   on your phone.

> ⚠️ The dashboard has **no password**. Anyone on the same network can press Run or Reset. Only do this on
> your home network, and never forward port 8000 on your router. To watch from outside the house, use a
> private network tool like Tailscale, not port forwarding.

## Day-to-day

- **Nothing to do day to day.** Rounds run at the scheduled times on weekdays (US-Eastern, whatever the PC's own
  timezone). Season 1 ends 30 days after the first round, and the next season starts automatically from each
  faction's ending balance.
- **Glance at the dashboard** when you like. The Comms panel shows every debate, and the standings show who's
  winning.
- **Watch for "using rule-based view" notices** in Comms. They mean a Claude call failed, usually because the
  plan's usage limit was hit, and that faction fell back to its built-in rules for that call. A few are fine.
  If you see many, switch to Sonnet or drop to one or two rounds a day, or the Legion is no longer really
  being tested.
- **Back up** the `prop_desk\data` folder now and then. It holds the ledgers, track records, season history
  and every saved round transcript.
- **Updating:** close the black window, run `git pull` in the `prop-desk-arena` folder, then start the
  launcher again. Your data is kept.
- **Stopping:** close the black window. Disable the Task Scheduler task if you don't want it starting at
  sign-in.

## Troubleshooting

| What you see | What to do |
|---|---|
| The badge says **Rules (offline)** | Claude isn't signed in. Run `claude auth login`, then restart the Arena. |
| The badge says **data: synthetic** | The PC can't reach Yahoo Finance. Check the internet connection. Antivirus or a work network may block `query1.finance.yahoo.com`, `query2.finance.yahoo.com` or `fc.yahoo.com`. |
| **markets: synthetic** | The same, for `api.elections.kalshi.com`. Or turn prediction markets off with `PROP_DESK_KALSHI=false`. |
| `localhost:8000` won't load | The black window was closed, or the Arena crashed. Check `prop_desk\arena.log` (open it with Notepad). |
| Error about **port 8000** | Something else uses that port. Add `PORT=8001` to `arena.env` and use `http://localhost:8001`. |
| Rounds didn't run | The PC was asleep or off, or it's a weekend. Check the sleep setting and that the black window is open. |
| Something else | Run `.venv\Scripts\python -m prop_desk.check`. It checks everything above in one go. |
