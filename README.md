# cxgear

Independent ChatGPT account rotation for Codex CLI. Use your available weekly
and short-window allowances across accounts without teaching your projects or
agent framework anything about account management.

Inspired by ccgear's account-selection policy. cxgear uses Codex's own login,
quota reporting, app-server, and terminal UI. It doesn't depend on ccgear or Spark.

## Install

Requires Linux, Python 3.11+, and an installed Codex CLI. The native integration
suite is verified against **Codex 0.155.1**. Other versions aren't yet verified;
the external-auth and remote-client interfaces are experimental.

```sh
git clone https://github.com/tuncenator/cxgear.git ~/Programs/cxgear
cd ~/Programs/cxgear
./install.sh
```

This installs `cxgear` into `~/.local/bin`. Ensure that directory is on PATH.
Add accounts using normal Codex browser login:

```sh
cxgear login work
cxgear login personal
cxgear status
```

Or link an existing **file-backed ChatGPT login**:

```sh
cxgear import work --from ~/.codex/auth.json
cxgear login personal
```

An import links the original credential file; it doesn't create a second copy
of a rotating refresh token. Keyring-only credentials need a separate
`cxgear login NAME`. Login runs the official Codex CLI, including
`cxgear login personal --device-auth` for a device flow.

Try it without changing your regular command:

```sh
cxgear codex
cxgear codex exec --json 'Describe this repository.'
```

Then enable transparent use:

```sh
cxgear install
codex
```

`cxgear install` adds a private launcher at `~/.local/share/cxgear/bin/codex`
and a marked PATH block to Bash and Zsh startup files. It leaves native Codex's
`~/.local/bin/codex` under the native installer's control, so updates cannot
replace cxgear's launcher. The upstream updater-managed executable is used
directly. Existing installations are migrated and their original native launcher
is restored from the saved backup.

New shells pick it up automatically. For an already-open shell, run the `source`
command printed by installation (normally `source ~/.local/share/cxgear/env.sh`).
`cxgear codex resume` also works immediately without reloading the shell.
`cxgear uninstall` removes only its launcher and marked startup blocks; it
preserves other shell settings and any newer native Codex installation.
No project configuration, hooks, or framework integration is required.

`cxgear status` checks both the installed launcher and command resolution on the
current PATH. `SELECTED` describes the broker's choice for managed sessions, not
the login used by every Codex process. Existing native sessions require exit and
resume through cxgear. The advanced `--bin-dir` install keeps the legacy shim
behavior at an explicitly chosen location; it does not configure shell PATH.

## Controls

`status` and `watch` show account labels and reported plans, quota bars, remaining
allowance, reset times, a weekly pace guide, and whether the launcher is enabled.
Missing short windows are marked as not reported; stale readings never imply
current headroom. Colors follow your terminal and respect `NO_COLOR`.

Weekly bars use ccgear's GPT palette: muted magenta `█` for usage within pace,
dark purple `▒` for an unused below-pace gap, bright pink `▓` for usage above
pace, and gray `░` for the remaining allowance beyond both usage and pace.
Each account uses its own reset time against a linear seven-day pace guide.
The textures remain distinct in plain output, and status includes a legend.

Account names such as `existing` are aliases you choose, not subscription types.
Set a display label without changing login storage or existing commands:

```sh
cxgear configure existing --label Work
```

The heading becomes `Work (existing) · Pro` if Codex reports the Pro plan.
`status --json` includes labels and plans, but never login tokens.

| Command | Purpose |
| --- | --- |
| `cxgear status` / `status --json` | Accounts, usage, resets, and current decision |
| `cxgear watch` | Refreshing terminal dashboard |
| `cxgear refresh` | Fetch fresh quotas and reconsider the active account |
| `cxgear pin personal` | Switch live sessions to this account and stay there |
| `cxgear auto` | Return to automatic selection |
| `cxgear dryrun` | Explain the next selection using the broker's current readings |
| `cxgear indicator` | Waybar-compatible JSON with account and quota tooltip |
| `cxgear log` | Recent usage readings and switch reasons, without credentials |
| `cxgear doctor` | Native executable, state directory, and launcher paths |
| `cxgear stop` | Stop background polling; sessions retain their current login |

Status starts the account broker if needed. `dryrun` doesn't issue an additional
switch, but the broker continues its normal automatic policy in the background.
After `stop`, another cxgear command or new session starts the broker again.

Use `CXGEAR_ACCOUNT=personal codex` to pin just one launch. Global account changes
reach connected sessions in roughly two seconds, outside network delays.

## Selection policy

Each account's native Codex quota is polled about once a minute. Automatic
selection follows ccgear's percentage/reset policy:

* Prefer the higher `remaining weekly percentage / hours until reset`.
  Plan multipliers do not weight this score. With 41% remaining and 121 hours
  on work versus 100% and 168 hours on personal, personal wins.
* Switch away at 90% short-window usage or 96% weekly usage. Normal pacing
  switches require the target's short-window usage below 80% and weekly usage
  below 96%. Escaping a short cap permits a target below 90%; escaping a
  weekly cap permits a short-capped target with weekly allowance remaining.
* Prioritize a target resetting within 24 hours when the current account has
  at least three times as long until reset, subject to normal target guards.
* Suppress small pressure differences using ccgear's deadband:
  `2 / max(shorter remaining window in hours, 1)`. There is no time cooldown.
  Pressure calculations floor time until reset at one minute.
* When both accounts are short-capped, drain the first registered account to
  98% first. When both are weekly-capped, drain it to 99% first. This account
  occupies ccgear's Max role. A fallback after that requires explicit
  extra-usage opt-in; cxgear doesn't assume ccgear's Team billing setup.
* Prefer an explicitly reported dormant account (0% with no reset yet).
  Missing, expired, failed, or stale observations remain unknown, not dormant.
  If the active account is unavailable, use a healthy account; if all are
  unavailable, hold. A manual pin takes precedence.

The threshold ordering, emergency exceptions, perishable priority, dormant
handling, and deadband match ccgear for two accounts with valid observations.
For additional accounts, cxgear ranks eligible candidates by the same score.
Native quota freshness checks and explicit extra-usage opt-in are retained.

Plan capacity is optional **display metadata only**. For example:

```sh
cxgear configure work --capacity 20
cxgear configure personal --capacity 5
```

These values label the plans in status; they never influence account selection
and do not represent measured weekly token caps.

Paid extra usage is never a preferred fallback unless you opt it in:

```sh
cxgear configure work --extra-usage on
```

This only changes account selection. It does not enable billing, purchase
credits, impose a spending cap, or prevent Codex from using credits already
enabled on the current account. When all accounts are unavailable, cxgear holds
the current account and reports why; provider limits remain authoritative.

## How live switching works

The launcher starts an upstream Codex app-server on a private Unix socket.
The unmodified interactive Codex UI connects to that server. A separate local
controller supplies the selected ChatGPT credentials through Codex's
`account/login/start` external-auth interface.

Changing `auth.json` alone doesn't reliably change an already-running Codex
process. Updating the live app-server authentication does: the next model
request uses the new account while the thread and completed tool work remain
in the same server. An in-flight HTTP request finishes under its original
account. cxgear does not cancel and replay tools to switch accounts.

There is one app-server per launch, because its authentication is server-wide.
All child threads in a launch share its selected account. A per-user broker
manages account login state, quota observations, and selection. Native Codex
performs token refresh; credentials travel only through private local IPC.

The caller's `CODEX_HOME`, working directory, configuration, and session storage
are preserved. Account login homes are separate. There are no Spark-specific
branches or dependencies.

## Compatibility boundaries

* **Interactive sessions:** the normal Codex UI, including resume/fork, connects
  through native `--remote`. To adopt cxgear for a session started earlier, exit
  and resume it through the launcher. Existing standalone processes cannot be
  attached to retroactively.
* **Unattended work:** `codex exec`, resume, and fork use an app-server adapter,
  because native exec doesn't expose a remote-server option. It supports stdin,
  JSONL events, model/config/feature overrides, explicit sandbox options, image
  input, output schemas, saved final messages, and interruption/failure exits.
  Text mode is a concise response stream; not every native progress event is
  reproduced. Usage totals are cumulative across a resumed thread, matching
  native exec. This isn't a byte-for-byte clone of every CLI interface.
* **Native fallback:** unknown exec options, exec review, profiles, worktree
  launches, and special configuration/trust switches use upstream Codex with a
  notice and **without rotation**. Login, logout, update, app-server, and other
  administrative commands also remain native. API-key and local-model runs,
  explicit remote endpoints, and unconfigured installations pass through.
  An unrelated `OPENAI_API_KEY` exported in your shell does not disable ChatGPT
  rotation. Explicit Codex credential variables (`CODEX_API_KEY`,
  `CODEX_ACCESS_TOKEN`, `OPENAI_IDENTITY_TOKEN_FILE`) and saved non-ChatGPT logins
  retain native handling.
* **Failures:** quota polling reduces the chance of hitting a cap, but can't
  eliminate races with usage elsewhere. A failed turn is reported faithfully;
  cxgear does not automatically replay it. Resume it after selecting an account.
* **Platforms:** Linux is tested. Windows isn't supported; macOS is unverified.

`CXGEAR_BYPASS=1 codex ...` explicitly uses upstream Codex.
`CXGEAR_CODEX_BINARY=/absolute/path/to/codex` selects an upstream executable.

## Local data

By default, state lives in `~/.local/share/cxgear` (override `CXGEAR_HOME`):

```text
accounts/NAME/     Native Codex login homes; imported auth.json may be a symlink
config.json       Account names, plan metadata, and upstream binary path
state.json        Current account, manual pin, and last switch
events.jsonl      Numeric usage observations and account decisions
control.sock      Private broker socket
install.json      Reversible launcher installation record
```

Directories are owner-only; state files and the control socket use mode 0600.
Status and event logs exclude tokens, prompts, and model responses. Model traffic
goes from native Codex directly to its configured provider. Treat the account
directory as credentials; keep it out of version control. An imported credential
file retains its original permissions and location.

## Development and verification

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v

CXGEAR_TEST_CODEX=/absolute/path/to/native/codex \
  .venv/bin/python -m unittest discover -s tests -v
```

The native integration tests create temporary homes, synthetic credentials, and
a localhost model/quota server. They verify automatic switching in the middle
of a tool-using turn, retained history/tool results, the actual interactive UI,
exec JSON and resume, schema forwarding, final-output files, failed turns, and
termination without replay. They don't consume real subscription tokens.
Live operation with real accounts still requires signing in and observing your
own quotas; synthetic tests cannot establish provider acceptance of every plan.

The implementation follows OpenAI's [Codex authentication](https://learn.chatgpt.com/docs/auth)
and [app-server](https://learn.chatgpt.com/docs/app-server) interfaces, verified
against the [0.155.1 source](https://github.com/openai/codex/tree/rust-v0.155.1).

MIT licensed. Not affiliated with OpenAI.
