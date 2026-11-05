---
title: "From Screwdrivers to Python: How a Storage Array Made Me a Developer"
published: false
description: "I fixed servers with a screwdriver for years. Then two NetApp clusters forced me to learn to code - and my first real program is still running in production."
tags: career, python, powershell, sysadmin, automation
---

I'm the person you call when a server is physically broken. My toolbox has a screwdriver, spare RAM, a bootable USB with memtest, and zero textbooks on algorithms. I never planned to become a developer. For a long time, I wasn't one.

Then two storage arrays changed my career without asking for permission.

## Level 1: tech support and the psexec years

I started in the IT department of one of the largest retail companies in Ukraine. Thousands of employees, hundreds of stores, and a constant stream of tickets like "install Chrome on 40 PCs by Friday".

When you do the same thing 40 times, you learn the first rule of IT: **the machine will not judge your laziness.** So I wrote my first script - a `foreach` loop over `psexec`:

```powershell
$computers = Get-Content .\pcs.txt
foreach ($pc in $computers) {
    psexec \\$pc -s -d msiexec /i "\\fileserver\apps\chrome.msi" /quiet /norestart
}
```

`-s` runs as SYSTEM. `-d` doesn't wait. It was ugly, it was glorious, and it turned a three-hour Friday into a ten-minute coffee break. I was hooked.

Then came Active Directory utilities: creating users in bulk, group membership, password resets, reports. PowerShell got an `ActiveDirectory` module. Python started showing up too - for parsing CSV exports, generating logins, renaming files. Nothing academic. Every script was born from "I refuse to click this 200 times."

I didn't learn programming. I learned it by accident, one ugly script at a time.

## Level 2: hardware, SAN, and the wall of text

Eventually I moved from tech support to hardware and infrastructure: racks, fiber optics, servers, storage. That's where the fun ended.

The company had two NetApp clusters - C400 and C800. And the vendor's answer to everything was: "Use the CLI."

The CLI is fine. The problem is the *commands*. Creating one volume looks like this:

```text
volume create -vserver svm0 -volume ORACLE_DATA_01 -aggregate C400_02_01_SSD_CAP_1
  -size 500GB -security-style ntfs -autosize-mode off -snapshot-policy none
  -percent-snapshot-space 0 -qos-policy-group qos-gold-svm0
```

That's one line. For one volume. Now do it 30 times, and each time pray you didn't typo the aggregate name - because a volume on the wrong aggregate is a ticket, a rollback, and a very long evening.

QoS policies were fun too: bronze, silver, gold, platinum, vip - each with its own IOPS limit you had to remember. Gold is 10k IOPs, platinum is 20k, and "vip" is unlimited, obviously.

I typed these commands often enough to hate them. And one day I caught myself thinking: *the web UI is painful, the CLI is dangerous, and I'm the only one who can read this wall of text. What if I build a form?*

## Level 3: the NetApp utility - PowerShell edition

So I built a Windows tool: a form with tabs - "Create Volume", "Create LUN", "Map LUN", "Show LUN". Radio buttons for aggregates and QoS policies, so nobody has to remember that platinum is 937.5 Mb/s. It connected to the cluster over SSH and sent the commands.

The first version was PowerShell with WinForms. If you've ever built a GUI in PowerShell, you know the pain: `New-Object System.Windows.Forms.Label`, coordinates by hand, and a base64-encoded logo bigger than your script.

The interesting part was the SSH interaction. NetApp doesn't have a friendly API in this scenario - you open a shell and *read the output*. The trick: wait for a success marker:

```powershell
function send-commands {
    param($Command, $Stream, $Prompt, [int]$Timeout = 30)
    $Stream.writeline($Command)
    $found = $Stream.expect($Prompt, (New-TimeSpan -Seconds $Timeout))
    # $Prompt = 'Successful'            for volume create
    # $Prompt = 'Created a LUN of size' for lun create
}
```

`volume create` answers with `Successful`. `lun create` answers with `Created a LUN of size`. You wait for the marker, you read the output, you move on. It's `expect` from 1991, but it works - and it's still how I do it today.

The tool worked. Colleagues started using it. The wall of text became a form, and the form became a habit.

## The bugs that taught me more than any course

This is where I realized I was actually *programming* now - because real programs have real bugs, and real bugs teach you things.

**Bug 1: the +3% quirk.** Our process required volumes to be created with a 3% size buffer. `100GB` → `103GB`. The original script did `100 + 100 * 0.03 = 103.0` - but ONTAP's `-size` only accepts integers. `33GB` became `33.99GB`, and the array politely refused. The fix: `math.ceil(33 * 1.03) = 34`. I learned about integer math not from a textbook - from a storage array that doesn't do fractions.

**Bug 2: names.** What makes a valid volume name? I read the NetApp KB instead of guessing: starts with a Latin letter, then letters/digits/underscores, no hyphens, no spaces, max 203 characters. That became a regex:

```python
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,202}$")
```

**Bug 3: the false failure.** The first backend gave up after one second of silence from the array. On slow storage the command was still running - but the tool already reported failure. Users don't trust a tool that lies. The fix: wait for the marker until the 30-second deadline, and only use the "silence" heuristic for commands where output *is* the result:

```python
deadline = time.time() + timeout
buffer = ""
matched = None

while time.time() < deadline:
    if shell.recv_ready():
        buffer += shell.recv(65535).decode(errors="replace")
        if expect and expect in buffer:
            matched = expect
            break
    time.sleep(0.05)

success = (matched is not None) if expect else True
```

**Bug 4: my own reference code was wrong.** The original scripts had the LUN mapping fields swapped: the volume name went into `-lun` and the LUN name into `-volume`. It "worked" for years only because both names were usually identical. When I rewrote the tool, I fixed it - and documented why it was a deliberate divergence from the reference.

## The rewrite: PowerShell → Python

The PowerShell version did its job. But I wanted a real app: dark theme, three languages (UA/RU/EN), validation before the command ever touches the array, credentials that live only in memory.

So I rewrote it in Python with PySide6 and paramiko. One SSH session for the whole app instead of a new one per click. A fake backend so you can test the UI without a real cluster. An exe that runs on any Windows machine without Python installed.

The funniest part: a QA audit of the first version found one critical bug - the false failure I described above - plus a pile of notes. I was no longer a sysadmin with a script. I had a codebase, a changelog, and tests. Somewhere along the way, without a single CS course, I had become a developer.

## What I'd tell my old self

Sysadmins make great developers - probably better than most bootcamp grads, and I say that as someone who has been both:

- **You already know the problem domain.** Nobody needs to explain to you what a LUN is, why storage matters, or why your users are angry. Half of software engineering is understanding the problem; you've been living inside it for years.
- **Real pain beats tutorials.** Every script I wrote was a tool I actually needed. Motivation on demand - you can't buy that.
- **The machine will not judge your laziness.** Automate the boring stuff. That itch you feel when you type the same command for the 40th time? That's your brain telling you to write code.
- **You don't need permission.** Nobody asked me to build the NetApp utility. I built it because I was annoyed. That's a legitimate engineering process.

Today the little utility is still used in production. It has a changelog with bug numbers, a test suite, and a QA report. And I'm writing this article - not because I'm a developer now, but because I finally admitted it.

If you're a sysadmin reading this: that long command you keep pasting? Build a form around it. It'll take you a weekend, it'll save you a year, and it might just change your career.
