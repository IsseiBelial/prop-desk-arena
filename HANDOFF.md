# Handoff: set up the Prop Desk Arena on this PC

**Who this is for:** Claude Code (or a person) running on the spare Windows PC that will host the Arena.
Work through the steps in order. Every step ends with a **check**. Don't move on until the check passes. If a
check fails and the fix isn't obvious, stop and ask the owner. Don't guess.

**The owner:** a CTO at a nonprofit, a high-level engineer but not a day-to-day programmer. When you report
back, be brief and plain: what you did, what worked, and anything they must do themselves.

---

## 0. What you are setting up

The Prop Desk Arena is a paper-trading contest between three AI "factions":

| Faction | How it decides |
|---|---|
| Iron Legion | a council of Claude traders |
| The Ascendant | quant models |
| The Brood | 24 simple signal "drones" |

- **Money:** each faction starts with **$100 of pretend money** and trades stocks, crypto, currencies,
  options and Kalshi prediction markets.
- **Seasons:** 30 days long, won on a risk-adjusted score, with profits rolled into the next season.
- **The app:** a Python web server (FastAPI) with a browser dashboard at http://localhost:8000. It runs
  rounds at **9:45am and 3:30pm US-Eastern on weekdays**.
- **Code:** GitHub repo `IsseiBelial/vllmTesting`. The app is in `prop_desk/`. `README.md` explains the
  app; `SETUP.md` is the human version of this guide.

### Hard rules

1. **Paper trading only.** Don't connect a real brokerage, add API keys for trading, or change anything that
   moves real money.
2. **Don't commit secrets or data.** `prop_desk/arena.env`, `prop_desk/data/` and `prop_desk/arena.log` are
   git-ignored. Keep it that way.
3. **Don't push to GitHub** or change the repo unless the owner asks. This PC only runs the app.
4. **Claude usage costs the owner's plan.** A full round in Claude mode uses roughly $2.20 of usage at API
   prices, and takes about 5 minutes. Run **one** test round only (step 8), not several.
5. **Don't change trading rules or settings** beyond what this guide says, unless the owner asks.

---

## 1. Check what's already installed

Use PowerShell.

```powershell
py --version        # need Python 3.10+ (3.12 recommended)
git --version
claude --version    # Claude Code
```

Install anything that's missing:

```powershell
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
# Claude Code (only if missing):
irm https://claude.ai/install.ps1 | iex
```

If `winget` itself is missing, ask the owner to install Python from python.org (tick "Add python.exe to PATH")
and Git from git-scm.com.

After installing, open a **new** PowerShell window so the new programs are found.

**Check:** all three commands print a version, and `py -3.12 --version` (or `py --version`) shows 3.10 or newer.

## 2. Claude sign-in (the owner may need to do this)

The Arena reaches Claude through the owner's Claude plan, via the Claude Agent SDK. That needs Claude Code to
be signed in on this PC.

```powershell
claude auth status
```

- If it says you're signed in, you're done with this step.
- If not, the **owner** must run `claude auth login` themselves. It opens a browser to sign in. You can't do
  this for them; ask them and wait.

**Check:** `claude auth status` shows a signed-in account.

## 3. Get the code

```powershell
cd $HOME
if (Test-Path prop-desk-arena) { cd prop-desk-arena; git fetch origin } else {
  git clone https://github.com/IsseiBelial/vllmTesting.git prop-desk-arena; cd prop-desk-arena }
# The Arena lives on main once PR #1 is merged. Until then it's on the feature branch.
if (git ls-tree --name-only origin/main prop_desk) { git checkout main; git pull } else {
  git checkout ccr-ef4ab90f-8bj1g0; git pull }
```

- If git asks for GitHub sign-in, the repo is private and the owner has to sign in in the browser window it
  opens.
- If the repo has been **renamed** (for example to `prop-desk-arena`), use the new URL. GitHub also redirects
  the old one.

**Check:** `Test-Path prop_desk\server.py` prints `True`.

## 4. Install the Python parts

From the repo folder (`$HOME\prop-desk-arena`):

```powershell
py -3.12 -m venv .venv       # or: py -m venv .venv  if 3.12 isn't the installed version
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r prop_desk\requirements.txt
```

This takes a few minutes.

**Check:** `.venv\Scripts\python -m pytest prop_desk\tests -q` ends with **"38 passed"**. Some tests may have
been added since; the check is that none fail. The tests use simulated data and make **no** Claude calls.

## 5. Settings file

```powershell
if (-not (Test-Path prop_desk\arena.env)) { copy prop_desk\arena.env.example prop_desk\arena.env }
```

Leave the defaults unless the owner said otherwise:
- $100 per faction (`PROP_DESK_CASH=100`)
- 30-day seasons
- rounds at `09:45,15:30` US-Eastern
- model `claude-opus-5-5`
- every asset class on

If the owner wants to use less of their plan, change `PROP_DESK_MODEL=claude-sonnet-5-5` (about half the
usage).

**Check:** `Get-Content prop_desk\arena.env | Select-String "PROP_DESK_CASH|SCHEDULE"` shows `100` and
`09:45,15:30`.

## 6. Health check

```powershell
.venv\Scripts\python -m prop_desk.check
```

It must end with **"Ready."** Lines to look for:

| Line | Meaning |
|---|---|
| `Claude: using your Claude plan login` | Step 2 worked |
| `Live stock data: SPY last close $…` | Yahoo Finance is reachable |
| `Lively-stock screener`, `Live crypto`, `Live forex`, `Option chains` | Live data for each instrument. A `[WARN]` here is acceptable: that instrument falls back to a built-in list or a model price. Tell the owner about it. |
| `Live Kalshi data` | Prediction markets are reachable. A WARN means simulated Kalshi prices. Tell the owner, because then prediction-market results mean little. |

If a line says `[FAIL]`, follow the hint printed under it. A Yahoo failure usually means a firewall or
antivirus is blocking `query1.finance.yahoo.com`, `query2.finance.yahoo.com` or `fc.yahoo.com`.

**Check:** the last line says **Ready**.

## 7. Keep the PC awake and stop surprise restarts

```powershell
powercfg /change standby-timeout-ac 0      # never sleep when plugged in
powercfg /change hibernate-timeout-ac 0
```

Windows Update active hours (8am–6pm, so it won't restart during market hours) need an **admin** PowerShell:

```powershell
reg add HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings /v ActiveHoursStart /t REG_DWORD /d 8 /f
reg add HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings /v ActiveHoursEnd /t REG_DWORD /d 18 /f
```

If you don't have admin rights, ask the owner to set it instead: Settings → Windows Update → Advanced options →
Active hours → Manually → 8:00 AM to 6:00 PM.

**Check:** `powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE` shows the AC setting as `0x00000000`.

## 8. One test round, then reset

Start the server in the background, run **one** round through the API, then wipe the results so the real
season starts clean.

```powershell
$p = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "-m","prop_desk.server" -PassThru -WindowStyle Hidden
Start-Sleep 8
Invoke-RestMethod http://localhost:8000/api/state | Select-Object mode, running
```

`mode` should start with **"Claude plan"**. If it says "Rules (offline)", step 2 isn't done; stop and fix it.

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/session -ContentType "application/json" -Body '{"symbols":null}'
# Wait for the round to finish (about 5-8 minutes on the plan).
do { Start-Sleep 20; $s = Invoke-RestMethod http://localhost:8000/api/state } while ($s.running)
$s.standings | Select-Object name, equity, season_return_pct, score, cost_usd
```

Optionally, ask the owner to open http://localhost:8000 during the round so they can watch it.

Then reset everything and stop this test server:

```powershell
Invoke-RestMethod -Method Post http://localhost:8000/api/reset
Stop-Process -Id $p.Id
```

**Check:**
- The round finished.
- Every faction shows an equity near $100.
- `prop_desk\arena.log` has no Python tracebacks, if one exists yet.
- After the reset, `Invoke-RestMethod http://localhost:8000/api/state` showed equity back at $100. Run that
  before stopping the server.

A few notices in the round saying *"using rule-based view"* are fine. Many of them mean Claude calls are
failing. Report that to the owner.

## 9. Start the Arena automatically at sign-in

Register a scheduled task that runs the launcher. The launcher restarts the app if it ever crashes.

```powershell
$repo = "$HOME\prop-desk-arena"
$action   = New-ScheduledTaskAction -Execute "$repo\prop_desk\run_arena.bat" -WorkingDirectory $repo
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# ExecutionTimeLimit 0 = no limit. Without it Windows kills the task after 3 days.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "Prop Desk Arena" -Action $action -Trigger $trigger -Settings $settings -Description "Paper-trading contest; dashboard at http://localhost:8000" -Force
Start-ScheduledTask -TaskName "Prop Desk Arena"
```

If `Register-ScheduledTask` says access is denied, run that PowerShell as administrator.

**Check:**
- After about 10 seconds, `Invoke-RestMethod http://localhost:8000/api/state` answers.
- `(Get-ScheduledTask "Prop Desk Arena").Settings.ExecutionTimeLimit` is `PT0S`.

The server needs the owner to be **signed in to Windows**. The task runs at sign-in, and the Claude login
belongs to that Windows user. If the PC restarts, someone has to sign in again. Tell the owner this. Setting up
Windows auto-login is their call.

## 10. Report back to the owner

Send a short summary covering:

1. **Done:** each step and whether its check passed.
2. **Health check:** any `[WARN]` lines, quoted.
3. **The test round:** how long it took, `cost_usd` per faction, and any trades or notable messages.
4. **Next real round:** the next weekday at 9:45am or 3:30pm US-Eastern. Season 1 starts at that round.
5. **The dashboard:** http://localhost:8000 on this PC. Viewing it from other devices is optional and covered
   in `SETUP.md`. The dashboard has **no password**, so only do that on the home network.
6. **Anything they must do themselves:** for example `claude auth login`, Windows sign-in after restarts,
   active hours, or the GitHub sign-in.

---

## Later: day-to-day

| Task | How |
|---|---|
| Update | Stop the task (`Stop-ScheduledTask "Prop Desk Arena"`) and close any Arena window. Run `git pull` in the repo folder, then `.venv\Scripts\python -m pip install -r prop_desk\requirements.txt`. Then `Start-ScheduledTask "Prop Desk Arena"`. Ledgers and season history in `prop_desk\data` are kept. |
| Back up | Copy `prop_desk\data` somewhere safe now and then. |
| Logs | `prop_desk\arena.log` |
| Settings | `prop_desk\arena.env`. Restart the task after editing it. |
| Stop for good | `Disable-ScheduledTask "Prop Desk Arena"`, then stop the running process. |
| Something wrong | Run `.venv\Scripts\python -m prop_desk.check` first. The troubleshooting table at the end of `SETUP.md` covers the common cases. |
