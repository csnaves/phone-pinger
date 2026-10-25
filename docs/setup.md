# Remote dev + always-on service setup

This document covers configuring the Intel NUC so the `phone_pinger` app runs on boot and can be edited/debugged remotely without being physically logged in.

The stack:
- **NSSM** — wraps `uvicorn` as a Windows service that starts on boot and survives logout.
- **Windows OpenSSH Server** — shell access to the NUC.
- **Tailscale** — zero-config private network so the NUC is reachable from your laptop/phone anywhere.
- **VS Code Remote-SSH** — edit files on the NUC from your laptop as if they were local.

> All commands below run on the NUC in an **elevated Command Prompt** (right-click → Run as administrator) unless noted otherwise. If you see `Access is denied` on an `nssm set` / `sc` / `setx /M` / `dism` command, the shell isn't elevated.

## 1. Store iCloud secrets as system-wide env vars

```cmd
setx /M ICLOUD_EMAIL "your@email.com"
setx /M ICLOUD_PASSWORD "yourpassword"
setx /M ICLOUD_DEVICE_ID "<target-device-id>"
setx /M LOCATOR_TOKEN "<random-token>"
```

`ICLOUD_DEVICE_ID` is the target phone's iCloud device id. To find it, list the account's devices and copy the right one's `_content["id"]` (see README).

`LOCATOR_TOKEN` is the shared secret every endpoint requires. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. The service won't start without it.

Stored in the registry at `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`. Any admin on the box can read them — fine for a home NUC.

Note: `setx` does **not** update the current shell's environment. Open a fresh Command Prompt afterward to confirm:

```cmd
echo %ICLOUD_EMAIL%
```

## 2. Install NSSM and register the service

Download NSSM from https://nssm.cc/download, extract `nssm.exe` to `C:\tools\nssm.exe`.

```cmd
mkdir %USERPROFILE%\personal_workspace\logs

C:\tools\nssm.exe install LocatorService ^
  "%USERPROFILE%\personal_workspace\venv\Scripts\python.exe" ^
  "-m uvicorn phone_pinger.main:app --host 0.0.0.0 --port 8000"

C:\tools\nssm.exe set LocatorService AppDirectory "%USERPROFILE%\personal_workspace"
C:\tools\nssm.exe set LocatorService AppStdout   "%USERPROFILE%\personal_workspace\logs\locator.out.log"
C:\tools\nssm.exe set LocatorService AppStderr   "%USERPROFILE%\personal_workspace\logs\locator.err.log"
C:\tools\nssm.exe set LocatorService Start       SERVICE_AUTO_START

:: Services only see machine env vars from boot time, so pass the device id and token directly.
:: This replaces the whole list, so always include every variable.
:: PYTHONUNBUFFERED makes log output reach the log file immediately.
C:\tools\nssm.exe set LocatorService AppEnvironmentExtra "ICLOUD_DEVICE_ID=<target-device-id>" "LOCATOR_TOKEN=<random-token>" "PYTHONUNBUFFERED=1"

:: Run as your user so file ACLs on venv\ and icloud_cookies\ work
C:\tools\nssm.exe set LocatorService ObjectName ".\<windows-user>" "<your-windows-password>"

net start LocatorService
sc query LocatorService
```

Live-tail the log (cmd has no native tail; shell out to PowerShell for this one):

```cmd
powershell -NoProfile -Command "Get-Content %USERPROFILE%\personal_workspace\logs\locator.out.log -Tail 50 -Wait"
```

### Firewall: open port 8000 to the LAN and Tailscale only

```cmd
netsh advfirewall firewall add rule name="Phone Pinger (TCP 8000)" dir=in action=allow protocol=TCP localport=8000 profile=private,public remoteip=localsubnet,100.64.0.0/10,fd7a:115c:a1e0::/48
```

- `localsubnet` is the home Wi-Fi (the Shelly button and home devices). `100.64.0.0/10` and `fd7a:115c:a1e0::/48` are Tailscale's address ranges.
- `profile=private,public` covers both adapters: the NUC's Wi-Fi is on the **Public** profile and Tailscale is **Private**. Leave the Wi-Fi on Public: switching it to Private would also expose SSH to the whole Wi-Fi (see step 3).
- **When Windows pops up "Allow Python to communicate on these networks?", click Cancel.** Clicking Allow creates a rule that lets *every* Python program accept connections on *any* port from *any* address. The rule above already covers the service. If one was created by accident, delete it:
  ```cmd
  netsh advfirewall firewall delete rule name="python.exe" dir=in
  ```
- Windows doesn't firewall a machine's connections to itself, so test from another device, not from the NUC.

First boot will require 2FA. Hit `/icloud?token=<token>` from any machine on your LAN (or over Tailscale once that's set up) to enter the code. Cookies persist to `icloud_cookies\` so this won't re-prompt until Apple invalidates the session.

**Upgrading a service installed before the `phone_pinger` package** (it ran `locator:app`): point it at the new module, make sure `LOCATOR_TOKEN` is in `AppEnvironmentExtra` (above), then restart:

```cmd
C:\tools\nssm.exe set LocatorService AppParameters "-m uvicorn phone_pinger.main:app --host 0.0.0.0 --port 8000"
net stop LocatorService & net start LocatorService
```

### Service lifecycle commands

```cmd
net start LocatorService
net stop  LocatorService
sc query  LocatorService

:: Restart (cmd has no one-liner; stop then start)
net stop LocatorService && net start LocatorService

:: Remove the service entirely (if you need to reinstall from scratch)
net stop LocatorService
C:\tools\nssm.exe remove LocatorService confirm
```

## 3. Enable Windows OpenSSH Server

```cmd
dism /Online /Add-Capability /CapabilityName:OpenSSH.Server~~~~0.0.1.0
net start sshd
sc config sshd start= auto

:: Make PowerShell (not cmd) the default shell over SSH -- optional but nicer
reg add "HKLM\SOFTWARE\OpenSSH" /v DefaultShell /t REG_SZ /d "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" /f

:: Smoke test from the NUC (before password login is disabled below)
ssh <windows-user>@localhost
```

The built-in sshd firewall rule only covers the **Private** profile, so SSH is reachable over Tailscale but not from the home Wi-Fi (Public). That's intentional.

### Key-based auth

On your **laptop**:

```cmd
ssh-keygen -t ed25519
type %USERPROFILE%\.ssh\id_ed25519.pub
```

Copy the **whole line**, including the leading `ssh-ed25519 `. A line that starts with `AAAA...` (just the middle part) is silently ignored, and SSH falls back to asking for a password.

Then on the **NUC**:

If your Windows account is a standard user: put the key in `%USERPROFILE%\.ssh\authorized_keys` (create the file and `.ssh` folder if missing).

If your Windows account is an administrator (check with `net localgroup Administrators`), sshd **only** reads `C:\ProgramData\ssh\administrators_authorized_keys` and ignores the per-user file. In elevated cmd:

```cmd
echo <paste-the-whole-pubkey-line> > C:\ProgramData\ssh\administrators_authorized_keys
icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
```

(`>` replaces the file; use `>>` to add a second key.) sshd re-reads the file on every login, so no restart is needed.

Only Administrators and SYSTEM can read that file, so a non-elevated editor shows it as **empty**. To view or edit it, run `notepad C:\ProgramData\ssh\administrators_authorized_keys` from an elevated cmd.

Test from the laptop with password auth disabled for that one connection:

```cmd
ssh -o PreferredAuthentications=publickey -o PasswordAuthentication=no <windows-user>@nuc.<tailnet>.ts.net whoami
```

Then on the NUC, confirm the login used the key. The event log should say `Accepted publickey` (not `Accepted password`):

```cmd
powershell -Command "Get-WinEvent -LogName OpenSSH/Operational -MaxEvents 50 | Where-Object { $_.Message -match 'Accepted' } | Select-Object -First 3 TimeCreated, Message"
```

### Disable password login

Only after the key test passes. Do this while you're physically at the NUC, so you can undo it at the keyboard if something goes wrong.

```cmd
copy C:\ProgramData\ssh\sshd_config C:\ProgramData\ssh\sshd_config.bak
notepad C:\ProgramData\ssh\sshd_config
```

Change `#PasswordAuthentication yes` to `PasswordAuthentication no` (remove the `#`), save, then:

```cmd
net stop sshd && net start sshd
```

From the laptop, `ssh <windows-user>@nuc.<tailnet>.ts.net whoami` should log in without a password prompt, and `ssh -o PreferredAuthentications=password <windows-user>@nuc.<tailnet>.ts.net` should fail with `Permission denied (publickey...)`.

To undo: `copy C:\ProgramData\ssh\sshd_config.bak C:\ProgramData\ssh\sshd_config`, then restart sshd.

**With password login off, the laptop's key is the only way in over SSH.** Before replacing or reinstalling the laptop, add the new machine's key to `administrators_authorized_keys` (with `>>`) while the old one still works. Otherwise you'll need to be at the NUC.

## 4. Install Tailscale on the NUC

1. Go to https://tailscale.com → "Use Tailscale free" → sign in with Google/Microsoft/GitHub.
2. Download the Windows installer from https://tailscale.com/download/windows.
3. Run it, click the tray icon → "Log in", complete browser auth.
4. Note the NUC's assigned IP (`100.x.y.z`) and MagicDNS name (`<hostname>.<tailnet>.ts.net`) from the tray icon or https://login.tailscale.com/admin/machines.
5. In the admin console, click the NUC → disable key expiry so it doesn't disconnect every ~6 months.

## 5. Laptop setup

1. Install Tailscale on the laptop and log in with the same account.
2. Install the **Remote - SSH** extension in VS Code (`ms-vscode-remote.remote-ssh`).
3. Add this to `%USERPROFILE%\.ssh\config` (create if missing):

```
Host nuc
  HostName <nuc-magicdns-name>.<tailnet>.ts.net
  User <windows-user>
  IdentityFile ~/.ssh/id_ed25519
```

4. VS Code → `F1` → "Remote-SSH: Connect to Host" → `nuc` → open the project folder (e.g. `C:\Users\<windows-user>\personal_workspace`).

## 6. Verify end-to-end

```cmd
:: From the laptop, over Tailscale
curl -X POST -H "Authorization: Bearer <token>" http://nuc.<tailnet>.ts.net:8000/ping
```

Expected: `{"status":"Ping sent to iPhone"}` and the phone rings.

## 7. The remote-dev loop

Edit the code in `phone_pinger/` in VS Code (changes write directly to the NUC). Then from your SSH session or VS Code integrated terminal on the NUC:

```cmd
net stop LocatorService && net start LocatorService
powershell -NoProfile -Command "Get-Content %USERPROFILE%\personal_workspace\logs\locator.err.log -Tail 50 -Wait -Encoding UTF8"
```

You can also run `claude` directly in the SSH session — Claude Code on the NUC itself, driven from your laptop.

## Gotchas & daily use

### Daily use

| Task | How |
|------|-----|
| Ping the phone | The iPhone/Mac Shortcut ([below](#iphone--mac-shortcut)), or by hand: `POST http://nuc.<tailnet>.ts.net:8000/ping` with `Authorization: Bearer <token>`, Tailscale connected. **`http`, not `https`** — uvicorn serves plain HTTP (Tailscale already encrypts the traffic). |
| Re-authenticate with Apple | `/ping` returns 401 → open `http://nuc.<tailnet>.ts.net:8000/icloud?token=<token>` and enter the code. It goes to the account's trusted devices (possibly the target phone itself). The browser keeps the token in a cookie for 30 days, so later visits can use plain `/icloud`. |
| Deploy a code change | Edit over Remote-SSH, then in an elevated cmd: `net stop LocatorService && net start LocatorService`. Python only reads the code at startup — changes don't apply until restart. |
| Watch logs | `locator.out.log` has the app's timestamped output: each request (IP, status, duration, User-Agent), init, target device, pings. `locator.err.log` has uvicorn startup/shutdown and tracebacks. |
| Rotate the token | Update `LOCATOR_TOKEN` in both places (`setx /M` and `nssm set ... AppEnvironmentExtra`), restart the service, then update the Shelly URL and any Shortcuts. |

### iPhone / Mac Shortcut

A Shortcut keeps the URL and token so you never type them: tap it, say "Hey Siri, Ping Phone", or run it from the Mac. Build it once on the iPhone; iCloud syncs it to the Mac.

In the **Shortcuts** app, tap **+**, add these actions in order, and name it **Ping Phone** (the name is the Siri phrase):

1. **Connect to your Tailscale network** (an action from the Tailscale app). Lets the Shortcut work without Tailscale already on.
2. **Get Contents of URL**
   - URL: `http://nuc.<tailnet>.ts.net:8000/ping`. Type the `http://`; the service doesn't serve `https`.
   - Tap the arrow to expand it:
     - Method: **POST**
     - Headers: key `Authorization`, value `Bearer <token>` (the word `Bearer`, a space, then the token)
     - Request Body: leave empty
3. **Get Dictionary Value**: Get **Value** for `status` in *Contents of URL*. A successful ping returns `{"status": "Ping sent to iPhone"}`; anything else (401 iCloud re-auth, 403 bad token) has no `status`.
4. **If** *Dictionary Value* **has any value**
   - **Show Result**: `Done, it's ringing`
5. **Otherwise**
   - **Show Result**: `Something went wrong.`
6. **End If**
7. **Disconnect from your Tailscale network** (optional, see below).

**Show Result** is what Siri reads out loud when you run it by voice; run any other way, it's a quiet banner. Use **Speak Text** instead if you want it spoken every time.

**Ways to run it:**
- iPhone: Siri, a home screen icon or widget, **Back Tap** (Settings → Accessibility → Touch → Back Tap), a Control Center button (iOS 18+), or the Action Button.
- Mac: Spotlight ("Ping Phone"), the menu bar, a keyboard shortcut set in the Shortcut's details, or Terminal: `shortcuts run "Ping Phone"`.

**Things to know:**
- **The token sits in the Shortcut in plain text.** Never share it with "Copy iCloud Link": that uploads it, token included, to Apple, and anyone with the link can ring the phone. If you rotate the token, edit the header in one place; it syncs.
- **The disconnect step turns Tailscale off even if it was on before you ran the Shortcut.** Drop step 7 if you normally leave Tailscale connected. And if the request itself errors (NUC down, no network), the Shortcut stops there with Siri's generic "problem running the shortcut", so step 7 never runs and Tailscale stays connected.
- **"Something went wrong."** most often means iCloud needs re-authentication (`/ping` returned 401): open `http://nuc.<tailnet>.ts.net:8000/icloud`. If that page says no verification is needed, check the token in the header instead (a 403 in `locator.out.log`).
- Expect a few seconds before the result: the service replies only after Apple accepts the ping.

### Gotchas

- **Don't run the app manually on port 8000 while the service is running.** Both try to bind port 8000; the loser crash-loops with `[Errno 10048] ... only one usage of each socket address`. `net stop LocatorService` first, or use a different port for manual testing.
- **`https://` won't work.** Use `http://`.
- **Garbled emoji in logs (`ðŸŽ¯`)** is just PowerShell 5.1 misreading UTF-8. Add `-Encoding UTF8` to `Get-Content`.
- **Cold boot timing:** the service can start before the network is up, so the startup iCloud login fails. `/ping` retries the login itself, so the first ping after a reboot still works. Verified with a pull-the-plug test.
- **cmd vs PowerShell:** `Start-Service`, `Get-Service`, etc. are PowerShell-only. In cmd use `net start` / `net stop` / `sc query`.
- **Changing your Windows password** breaks the service logon. Re-run `C:\tools\nssm.exe set LocatorService ObjectName ".\<windows-user>" "<new-password>"`.
- **Device wiped/replaced:** `ICLOUD_DEVICE_ID` will no longer match and the log will show `⚠️ No device with id ...`. List devices, grab the new `_content["id"]`, and update it in both places: `setx /M ICLOUD_DEVICE_ID ...` and `nssm set LocatorService AppEnvironmentExtra ...` (step 2). Then restart the service.

### Survives reboot / power loss — checklist

All verified on the NUC:

- [x] `LocatorService`, `sshd`, `Tailscale` services set to **Auto** start
- [x] Tailscale **Run unattended** on (`tailscale debug prefs` shows `"ForceDaemon": true`)
- [x] Sleep and hibernate on AC set to **Never** (`powercfg /change standby-timeout-ac 0`, `powercfg /change hibernate-timeout-ac 0`)
- [x] BIOS **After Power Failure → Power On** (NUC powers up by itself when plugged in)
- [x] Unplug → replug → no login → `/ping` from laptop works

### Security state — checklist

All verified on the NUC:

- [x] Every endpoint requires `LOCATOR_TOKEN`. FastAPI's `/docs`, `/redoc` and `/openapi.json` are disabled.
- [x] SSH: key-only (`PasswordAuthentication no`), and reachable only over Tailscale (sshd rule is Private-profile only)
- [x] Firewall: only `Phone Pinger (TCP 8000)` admits traffic to the service, from `localsubnet` + Tailscale ranges. No blanket `python.exe` rules.
- [x] Dependencies pinned in `requirements.txt`

### If it stops working

Something answers on home Wi-Fi but not over Tailscale, or the other way round: check the firewall rule (step 2) is still there and enabled:

```cmd
netsh advfirewall firewall show rule name="Phone Pinger (TCP 8000)"
```

Then test from your phone on cellular (Wi-Fi off, Tailscale on), and from a device on the home Wi-Fi with Tailscale off.

A `403` in `locator.out.log` means the request reached the app but the token was missing or wrong. That's not the firewall. Check the Shelly URL or Shortcut has `?token=` / the `Authorization` header with the current `LOCATOR_TOKEN`.

### Optional housekeeping

- **Log rotation** (logs grow forever otherwise):
  ```cmd
  C:\tools\nssm.exe set LocatorService AppRotateFiles 1
  C:\tools\nssm.exe set LocatorService AppRotateBytes 1048576
  ```

## Pitfalls

- **Secrets in the registry**: Machine-scope env vars are in the clear for any admin. For stricter isolation, switch to per-service env vars: `nssm set LocatorService AppEnvironmentExtra ICLOUD_EMAIL=... ICLOUD_PASSWORD=...`.
- **NSSM `ObjectName` password**: stored encrypted in LSA secrets, not plaintext — but if you change your Windows password, re-run the `set ObjectName` command.
- **Keep `--host 0.0.0.0`**: the Shelly button reaches the service over the LAN at `<nuc-lan-ip>:8000` (see README). Binding to the Tailscale IP alone would break the button.
- **2FA re-prompts**: Apple invalidates sessions periodically. The `/icloud` endpoint handles this, but you need to be reachable on Tailscale to use it from off-LAN.
- **Access denied running `nssm set` / `sc` / `setx /M` / `dism` / `netsh advfirewall`**: the shell isn't elevated. These all need Administrator.
- **"Allow Python through the firewall?" popup**: click Cancel. See the firewall section in step 2.
- **`setx` doesn't update the current shell**: after step 1, open a fresh Command Prompt before continuing.
