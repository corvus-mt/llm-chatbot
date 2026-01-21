param(
  [Parameter(Mandatory = $true)]
  [string]$Title,
  [string]$Body = "",
  [string]$Branch = "",
  [string]$CommitMessage = ""
)

$ErrorActionPreference = 'Stop'

function Resolve-GhPath {
  $cmd = Get-Command gh -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Path
  }
  $fallback = "C:\Program Files\GitHub CLI\gh.exe"
  if (Test-Path $fallback) {
    return $fallback
  }
  throw "GitHub CLI not found. Install gh or add it to PATH."
}

function Invoke-Git {
  param([string[]]$GitArgs)
  & git @GitArgs
  if ($LASTEXITCODE -ne 0) {
    throw "git $($GitArgs -join ' ') failed."
  }
}

function Invoke-Gh {
  param([string[]]$GhArgs)
  & $script:gh @GhArgs
  if ($LASTEXITCODE -ne 0) {
    throw "gh $($GhArgs -join ' ') failed."
  }
}

$gh = Resolve-GhPath

Invoke-Git @("rev-parse", "--is-inside-work-tree") | Out-Null
Invoke-Git @("remote", "get-url", "origin") | Out-Null

$status = & git status --porcelain
if (-not $status) {
  throw "No changes to commit."
}

$defaultBranch = ""
$originHead = & git symbolic-ref "refs/remotes/origin/HEAD" 2>$null
if ($LASTEXITCODE -eq 0 -and $originHead) {
  $defaultBranch = ($originHead -replace "^refs/remotes/origin/", "").Trim()
} else {
  $defaultBranch = & $gh repo view --json defaultBranchRef -q ".defaultBranchRef.name"
  if (-not $defaultBranch) {
    throw "Could not determine default branch."
  }
}

if (-not $Branch) {
  $slug = ($Title.ToLower() -replace "[^a-z0-9]+", "-").Trim("-")
  if (-not $slug) {
    $slug = "change"
  }
  $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $Branch = "agent/$timestamp-$slug"
}

if (-not $Branch) {
  throw "Branch could not be determined."
}

& git show-ref --verify --quiet "refs/heads/$Branch"
if ($LASTEXITCODE -eq 0) {
  Invoke-Git @("checkout", $Branch)
} else {
  Invoke-Git @("checkout", "-b", $Branch)
}

Invoke-Git @("add", "-A")

if (-not $CommitMessage) {
  $CommitMessage = $Title
}

Invoke-Git @("commit", "-m", $CommitMessage)
Invoke-Git @("push", "-u", "origin", $Branch)

$createOutput = & $gh pr create --title $Title --body $Body --head $Branch --base $defaultBranch 2>&1
if ($LASTEXITCODE -ne 0) {
  if ($createOutput -match "already exists") {
    $createOutput = & $gh pr view $Branch --json url -q ".url" 2>&1
  } else {
    throw "Failed to create PR. Output: $createOutput"
  }
}

$prUrl = ($createOutput | Select-String -Pattern "https://github.com/.+/pull/\\d+" -AllMatches).Matches.Value | Select-Object -First 1
if (-not $prUrl) {
  $prUrl = & $gh pr view $Branch --json url -q ".url"
}

Write-Host "PR: $prUrl"

try {
  & $gh pr merge --auto --squash --delete-branch $Branch
  if ($LASTEXITCODE -ne 0) {
    throw "auto-merge failed"
  }
  Write-Host "Auto-merge enabled (will merge after required checks pass)."
} catch {
  Write-Warning "Auto-merge could not be enabled. Ensure 'Allow auto-merge' and required checks are configured."
}
