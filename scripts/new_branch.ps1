param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Feature,

    [ValidateRange(1, 99)]
    [int]$Sequence = 0
)

$ErrorActionPreference = "Stop"

function ConvertTo-Slug([string]$Value) {
    $slug = $Value.ToLowerInvariant() -replace "[^a-z0-9]+", "-"
    return $slug.Trim("-")
}

$repositoryRoot = (& git rev-parse --show-toplevel).Trim()
if (-not $repositoryRoot) {
    throw "Run this helper from inside the Git repository."
}

$repositorySlug = ConvertTo-Slug (Split-Path $repositoryRoot -Leaf)
$featureSlug = ConvertTo-Slug $Feature
if (-not $featureSlug) {
    throw "Feature must contain at least one letter or number."
}

$today = Get-Date
$date = "{0:D4}/{1:D2}/{2:D2}" -f $today.Year, $today.Month, $today.Day
$prefix = "$repositorySlug/$featureSlug/$date"

& git fetch --prune origin 2>$null
$branches = & git for-each-ref --format="%(refname:short)" refs/heads refs/remotes/origin
$dailyPattern = (
    "^" + [regex]::Escape($repositorySlug) +
    "/[^/]+/" + [regex]::Escape($date) + "/(\d+)$"
)
$largest = 0
foreach ($branch in $branches) {
    $name = $branch -replace "^origin/", ""
    if ($name -match $dailyPattern) {
        $largest = [Math]::Max($largest, [int]$Matches[1])
    }
}

$nextSequence = if ($Sequence -gt 0) { $Sequence } else { $largest + 1 }
$branchName = "{0}/{1:D2}" -f $prefix, $nextSequence
$collision = $branches | Where-Object {
    ($_ -replace "^origin/", "") -eq $branchName
}
if ($collision) {
    throw "Branch already exists locally or on origin: $branchName"
}

& git switch -c $branchName
if ($LASTEXITCODE -ne 0) {
    throw "Git could not create branch $branchName."
}
Write-Host "Created branch: $branchName"
