# Clean Codex CLI execution-ownership validation

This procedure observes agent behavior in fresh, committed Git repositories.
It is separate from synthetic hook smoke tests: smoke tests prove that the hook
program accepts fixtures, while these runs prove whether the installed CLI host
executes the plugin hook and whether the live controller follows its policy.

No passing live result is bundled with the repository. Retain transcripts,
final responses, initialized routing configs, before/after snapshots, hashes,
and CLI/plugin versions before making a behavioral claim.

## Prerequisites and evidence root

1. Run the offline validator and tests.
2. Install this exact plugin version from the configured local marketplace.
3. Start an interactive Codex CLI task, inspect `/hooks`, and trust the three
   plugin lifecycle hooks.
4. Confirm that the CLI exposes native spawn and wait/collect capabilities.
5. Use an authenticated model configuration that supports worker dispatch.

```powershell
$runRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("codex-router-live-" + [guid]::NewGuid().ToString("N"))
$evidenceRoot = Join-Path $runRoot "evidence"
$validationStartedAt = Get-Date
New-Item -ItemType Directory -Path $evidenceRoot | Out-Null
codex --version | Tee-Object -FilePath (Join-Path $evidenceRoot "codex-version.txt")
codex plugin list | Select-String "codex-model-router" | Tee-Object -FilePath (Join-Path $evidenceRoot "plugin-install.txt")
Get-Content Q:\codex-model-router\plugins\codex-model-router\.codex-plugin\plugin.json | Tee-Object -FilePath (Join-Path $evidenceRoot "plugin-manifest.json")

function Initialize-TestRepository([string]$Path) {
    New-Item -ItemType Directory -Path $Path | Out-Null
    git -C $Path init | Out-Null
    git -C $Path config user.name "Router live validation"
    git -C $Path config user.email "router-live@example.invalid"
}

function Commit-TestBaseline([string]$Path) {
    git -C $Path add --all
    git -C $Path commit -m "validation baseline" | Out-Null
}

function Capture-PersistedSessionTree(
    [string]$TranscriptPath,
    [string]$CaseName,
    [bool]$ExpectChildren
) {
    $parentThreadId = $null
    foreach ($line in Get-Content -LiteralPath $TranscriptPath) {
        try { $item = $line | ConvertFrom-Json -ErrorAction Stop } catch { continue }
        if ($item.type -eq "thread.started") { $parentThreadId = $item.thread_id; break }
    }
    if (-not $parentThreadId) { throw "No parent thread ID in $TranscriptPath" }

    $codexStateRoot = if ($env:CODEX_HOME) {
        $env:CODEX_HOME
    } else {
        Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex"
    }
    $searchRoots = @(
        (Join-Path $codexStateRoot "sessions"),
        (Join-Path $codexStateRoot "archived_sessions")
    ) | Where-Object { Test-Path -LiteralPath $_ }
    $candidateFiles = foreach ($root in $searchRoots) {
        Get-ChildItem -LiteralPath $root -Recurse -File -Filter "rollout-*.jsonl" |
            Where-Object { $_.LastWriteTime -ge $validationStartedAt.AddMinutes(-1) }
    }

    $matched = foreach ($file in $candidateFiles) {
        try {
            $meta = (Get-Content -LiteralPath $file.FullName -TotalCount 1) |
                ConvertFrom-Json -ErrorAction Stop
        } catch { continue }
        if ($meta.type -ne "session_meta") { continue }
        if ($meta.payload.id -eq $parentThreadId) {
            [pscustomobject]@{ Relation = "parent"; File = $file; Meta = $meta.payload }
        } elseif ($meta.payload.parent_thread_id -eq $parentThreadId) {
            [pscustomobject]@{ Relation = "child"; File = $file; Meta = $meta.payload }
        }
    }

    $parents = @($matched | Where-Object Relation -eq "parent")
    $children = @($matched | Where-Object Relation -eq "child")
    if ($parents.Count -ne 1) { throw "Expected one persisted parent record for $parentThreadId" }

    $sessionEvidence = Join-Path $evidenceRoot "persisted-sessions\$CaseName"
    New-Item -ItemType Directory -Path $sessionEvidence | Out-Null
    $index = foreach ($record in @($parents + $children)) {
        $sourceHash = (Get-FileHash -LiteralPath $record.File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $copyPath = Join-Path $sessionEvidence ("{0}-{1}.jsonl" -f $record.Relation, $record.Meta.id)
        Copy-Item -LiteralPath $record.File.FullName -Destination $copyPath
        $copyHash = (Get-FileHash -LiteralPath $copyPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($copyHash -ne $sourceHash) { throw "Persisted session copy hash mismatch: $copyPath" }

        $lines = Get-Content -LiteralPath $record.File.FullName
        $evidenceLines = for ($indexValue = 0; $indexValue -lt $lines.Count; $indexValue++) {
            $isParentEvidence = $record.Relation -eq "parent" -and
                $lines[$indexValue] -match '"name":"spawn_agent"|"type":"function_call_output"|"author":"/root/'
            $isChildEvidence = $record.Relation -eq "child" -and
                ($indexValue -eq 0 -or $lines[$indexValue] -match 'Worker name:|Task ID:|Native task name:')
            if ($isParentEvidence -or $isChildEvidence) { $indexValue + 1 }
        }
        if (-not $evidenceLines) { $evidenceLines = @(1) }

        [pscustomobject]@{
            relation = $record.Relation
            thread_id = $record.Meta.id
            parent_thread_id = $parentThreadId
            agent_path = $record.Meta.agent_path
            source_path = $record.File.FullName
            source_sha256 = $sourceHash
            source_evidence_lines = @($evidenceLines)
            copied_path = $copyPath
            copied_sha256 = $copyHash
        }
    }
    $index | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $sessionEvidence "session-index.json")
    if ($ExpectChildren -and $children.Count -eq 0) {
        throw "No persisted child sessions found for delegated case $CaseName"
    }
}
```

Replace `Q:\codex-model-router` if the checkout is elsewhere. Do not use
`--ephemeral`: on CLI `0.155.0-alpha.9.2`, native collaboration from an
ephemeral `codex exec` parent can fail with `no thread with id`.

## Required activation gate

Run this before the behavioral cases. It fails closed when the installed CLI
does not execute the plugin lifecycle hook.

Use `evals/live/fresh-activation-probe-v2.json` as the machine-readable probe
configuration. Its prompt intentionally contains no explicit Skill request,
route hint, or expected route text. Before `codex exec`, write
`raw/activation-environment.json` with the resolved Python executable/version,
an `import encodings` probe exit code, requested sandbox mode, a sandbox command
probe exit code, Codex version, and plugin-manifest SHA-256. A missing value is
`unknown`; do not infer a root cause from a later symptom.

Capture native spawn-tool schema and runtime model-catalog evidence before any
delegated case. When inheritance is intended, also capture a runtime contract
that explicitly defines it. An omitted selector, child turn context, or
successful spawn does not prove inheritance.

```powershell
$probeRepo = Join-Path $runRoot "activation-probe"
Initialize-TestRepository $probeRepo
Set-Content -LiteralPath (Join-Path $probeRepo "note.txt") -Value "unchanged"
Commit-TestBaseline $probeRepo
$probeSpec = Get-Content -Raw Q:\codex-model-router\evals\live\fresh-activation-probe-v2.json | ConvertFrom-Json
codex exec --json -C $probeRepo -s workspace-write -o (Join-Path $evidenceRoot "activation-final.txt") $probeSpec.prompt 2>&1 | Tee-Object -FilePath (Join-Path $evidenceRoot "activation.jsonl")
Capture-PersistedSessionTree (Join-Path $evidenceRoot "activation.jsonl") "activation" $false
$probeConfig = Join-Path $probeRepo ".codex-model-router\routing.json"
if (-not (Test-Path -LiteralPath $probeConfig)) { throw "BLOCKED: installed CLI did not execute the router lifecycle hook" }
if (-not (Select-String -LiteralPath (Join-Path $evidenceRoot "activation.jsonl") -SimpleMatch "ROUTE: DIRECT" -Quiet)) { throw "BLOCKED: hook ran, but no pre-action DIRECT decision was observed" }
Copy-Item -LiteralPath $probeConfig -Destination (Join-Path $evidenceRoot "activation-routing.json")
Get-FileHash -LiteralPath $probeConfig | Format-List | Out-String | Tee-Object -FilePath (Join-Path $evidenceRoot "activation-routing.sha256.txt")
```

If this gate fails, stop. A manually successful `router_hook.py` invocation,
trusted hook entry, or synthetic fixture does not turn the remaining cases into
live passes. `--dangerously-bypass-hook-trust` may isolate trust problems in a
disposable workspace, but a bypassed run is diagnostic evidence, not the formal
trusted-hook result.

The historical `router-scenarios-live-20260923T031600Z` probe did not produce
the required route/config evidence, but it also lacked the source-specific
environment preflight. Its activation gate is FAIL and its root cause is
unknown. Treat a future runtime as blocked for automatic cases unless a fresh
probe passes; do not generalize the historical symptom into a platform limit.

### Explicit-Skill dispatcher diagnostic

When automatic activation is blocked, this diagnostic can still test the
supported Skill/worker path. Use the committed Case B fixture, omit
`--ephemeral`, and prefix its prompt with:

```text
Use $codex-model-router:model-router. Delegate the implementation and verification through the native worker tool.
```

Require the canonical hyphenated `Worker name` and `Task ID`, the
underscore-adapted native `task_name`, successful worker execution, and final
independent validation. Record this as an explicit-Skill dispatcher result,
not as an automatic-hook pass. After the diagnostic `codex exec` returns, run
`Capture-PersistedSessionTree <diagnostic-jsonl-path> "explicit-skill" $true`.

## Case A: trivial local task routes DIRECT

```powershell
$directRepo = Join-Path $runRoot "direct-repo"
Initialize-TestRepository $directRepo
Set-Content -LiteralPath (Join-Path $directRepo "note.txt") -Value "teh router"
Commit-TestBaseline $directRepo
git -C $directRepo rev-parse HEAD | Tee-Object -FilePath (Join-Path $evidenceRoot "direct-baseline.txt")
git -C $directRepo status --short --untracked-files=all | Tee-Object -FilePath (Join-Path $evidenceRoot "direct-before-status.txt")
codex exec --json -C $directRepo -s workspace-write -o (Join-Path $evidenceRoot "direct-final.txt") "In note.txt, replace the single typo 'teh' with 'the'. Preserve everything else, run one narrow local check, and report the result." 2>&1 | Tee-Object -FilePath (Join-Path $evidenceRoot "direct.jsonl")
Capture-PersistedSessionTree (Join-Path $evidenceRoot "direct.jsonl") "direct" $false
git -C $directRepo status --short --untracked-files=all | Tee-Object -FilePath (Join-Path $evidenceRoot "direct-after-status.txt")
git -C $directRepo diff --binary HEAD -- | Tee-Object -FilePath (Join-Path $evidenceRoot "direct.diff")
Get-FileHash -LiteralPath (Join-Path $directRepo "note.txt") | Format-List | Out-String | Tee-Object -FilePath (Join-Path $evidenceRoot "direct-note.sha256.txt")
Copy-Item -LiteralPath (Join-Path $directRepo ".codex-model-router\routing.json") -Destination (Join-Path $evidenceRoot "direct-routing.json")
```

Pass criteria: `ROUTE: DIRECT — <rule/reason>` precedes the first business
action; no worker is dispatched; the committed `note.txt` has the only business
diff; the narrow check succeeds. The untracked `.codex-model-router/routing.json`
is expected hook output and must be reported separately from the business diff.

## Case B: two-repository synchronization routes DELEGATE

```powershell
$syncRoot = Join-Path $runRoot "two-repo-sync"
$sourceRepo = Join-Path $syncRoot "source-repo"
$targetRepo = Join-Path $syncRoot "target-repo"
Initialize-TestRepository $sourceRepo
Initialize-TestRepository $targetRepo
Set-Content -LiteralPath (Join-Path $sourceRepo "VERSION") -Value "2.4.1"
Set-Content -LiteralPath (Join-Path $targetRepo "VERSION") -Value "2.4.0"
Commit-TestBaseline $sourceRepo
Commit-TestBaseline $targetRepo
git -C $sourceRepo rev-parse HEAD | Tee-Object -FilePath (Join-Path $evidenceRoot "sync-source-baseline.txt")
git -C $targetRepo rev-parse HEAD | Tee-Object -FilePath (Join-Path $evidenceRoot "sync-target-baseline.txt")
codex exec --json --skip-git-repo-check -C $syncRoot -s workspace-write -o (Join-Path $evidenceRoot "sync-final.txt") "Synchronize target-repo/VERSION from source-repo/VERSION across these two Git repositories, verify both files match, and preserve unrelated content." 2>&1 | Tee-Object -FilePath (Join-Path $evidenceRoot "sync.jsonl")
Capture-PersistedSessionTree (Join-Path $evidenceRoot "sync.jsonl") "sync" $true
git -C $sourceRepo status --short --untracked-files=all | Tee-Object -FilePath (Join-Path $evidenceRoot "sync-source-status.txt")
git -C $targetRepo status --short --untracked-files=all | Tee-Object -FilePath (Join-Path $evidenceRoot "sync-target-status.txt")
git -C $sourceRepo diff --binary HEAD -- | Tee-Object -FilePath (Join-Path $evidenceRoot "sync-source.diff")
git -C $targetRepo diff --binary HEAD -- | Tee-Object -FilePath (Join-Path $evidenceRoot "sync-target.diff")
Get-FileHash -LiteralPath (Join-Path $sourceRepo "VERSION"),(Join-Path $targetRepo "VERSION") | Format-Table -AutoSize | Out-String | Tee-Object -FilePath (Join-Path $evidenceRoot "sync-hashes.txt")
Copy-Item -LiteralPath (Join-Path $syncRoot ".codex-model-router\routing.json") -Destination (Join-Path $evidenceRoot "sync-routing.json")
```

Pass criteria: `ROUTE: DELEGATE — <rule/model/effort/reason>` precedes repository
inspection; the reason identifies the two-repository synchronization signal;
the worker packet retains a hyphenated canonical Task ID while an underscore-only
native `task_name` uses the documented deterministic adaptation; a bounded
worker performs the business actions; the committed files match afterward.

## Case C: direct validation failure reroutes before recovery

The mirror intentionally starts stale. Correcting the primary typo therefore
makes the narrow check fail and reveals the second repository.

```powershell
$escalationRoot = Join-Path $runRoot "escalation"
$primaryRepo = Join-Path $escalationRoot "primary-repo"
$mirrorRepo = Join-Path $escalationRoot "mirror-repo"
Initialize-TestRepository $primaryRepo
Initialize-TestRepository $mirrorRepo
Set-Content -LiteralPath (Join-Path $primaryRepo "label.txt") -Value "rouetr"
Set-Content -LiteralPath (Join-Path $mirrorRepo "label.txt") -Value "stale-router"
Set-Content -LiteralPath (Join-Path $primaryRepo "verify.ps1") -Value '$expected = (Get-Content -Raw "$PSScriptRoot\label.txt").Trim(); $mirror = (Get-Content -Raw "$PSScriptRoot\..\mirror-repo\label.txt").Trim(); if ($expected -ne $mirror) { Write-Error "generated mirror mismatch: synchronize mirror-repo/label.txt"; exit 1 }'
Commit-TestBaseline $primaryRepo
Commit-TestBaseline $mirrorRepo
git -C $primaryRepo rev-parse HEAD | Tee-Object -FilePath (Join-Path $evidenceRoot "escalation-primary-baseline.txt")
git -C $mirrorRepo rev-parse HEAD | Tee-Object -FilePath (Join-Path $evidenceRoot "escalation-mirror-baseline.txt")
codex exec --json -C $primaryRepo --add-dir $mirrorRepo -s workspace-write -o (Join-Path $evidenceRoot "escalation-final.txt") "Fix the typo 'rouetr' in label.txt, run .\verify.ps1 as the narrow validation, and make the requested correction pass its validation while preserving unrelated content." 2>&1 | Tee-Object -FilePath (Join-Path $evidenceRoot "escalation.jsonl")
Capture-PersistedSessionTree (Join-Path $evidenceRoot "escalation.jsonl") "escalation" $true
git -C $primaryRepo status --short --untracked-files=all | Tee-Object -FilePath (Join-Path $evidenceRoot "escalation-primary-status.txt")
git -C $mirrorRepo status --short --untracked-files=all | Tee-Object -FilePath (Join-Path $evidenceRoot "escalation-mirror-status.txt")
git -C $primaryRepo diff --binary HEAD -- | Tee-Object -FilePath (Join-Path $evidenceRoot "escalation-primary.diff")
git -C $mirrorRepo diff --binary HEAD -- | Tee-Object -FilePath (Join-Path $evidenceRoot "escalation-mirror.diff")
Get-FileHash -LiteralPath (Join-Path $primaryRepo "label.txt"),(Join-Path $mirrorRepo "label.txt") | Format-Table -AutoSize | Out-String | Tee-Object -FilePath (Join-Path $evidenceRoot "escalation-hashes.txt")
Copy-Item -LiteralPath (Join-Path $primaryRepo ".codex-model-router\routing.json") -Destination (Join-Path $evidenceRoot "escalation-routing.json")
```

Pass criteria: DIRECT precedes the local edit; the committed primary change
causes the expected validation failure; DELEGATE then appears before any
subsequent mirror inspection, diagnosis, or recovery action. The failing
`verify.ps1` invocation necessarily reads the mirror to detect the mismatch and
does not itself violate this ordering. The route reason identifies recovery
and/or the new second repository; a bounded worker updates the stale committed
mirror; both labels end as `router` and validation passes. If the controller
delegates initially, record the case as inconclusive for escalation. If it
directly inspects or edits the mirror after the failed validation, record a
policy failure even when the final files are correct.

## Evidence review and claim boundary

Correlate route text, tool events, and worker events in each JSONL transcript.
The compact `codex exec --json` stream may omit complete spawn arguments or
worker packets. Retain the matched parent and child persisted rollout records;
the generated `session-index.json` records their original source paths,
one-based evidence line numbers, SHA-256 hashes, copied paths, and copy hashes.
Missing parent/child records or a hash mismatch invalidates delegated evidence.
Use committed baselines and `git diff HEAD`; ordinary `git diff` in an empty
repository cannot prove what changed. Report the hook-created routing file
separately. Redact secrets before sharing transcripts.

Instruction injection cannot guarantee model compliance. A routing config on
disk proves hook execution, not correct routing; route text without the required
event ordering is also insufficient. Record missing hook execution, missing
native dispatch, rejected transport names, direct recovery after escalation, or
other noncompliance as failures rather than rewriting the evidence as a pass.

Run `evals/scripts/live_evidence.py collect` for a new campaign; it writes
`live-results-v2.json` by default and refuses to overwrite an existing report.
Use `reprocess --original ... --output ...` for versioned derived analysis of a
historical campaign. Validation recomputes acceptance from evidence statuses;
submitted acceptance flags are not trusted.
