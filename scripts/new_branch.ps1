param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Feature
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

$date = Get-Date -Format "yyyy-MM-dd"
$prefix = "$repositorySlug-$featureSlug-$date"

& git fetch --prune origin 2>$null
$branches = & git for-each-ref --format="%(refname:short)" refs/heads refs/remotes/origin
$pattern = "^" + [regex]::Escape($prefix) + "-(\d+)$"
$largest = 0
foreach ($branch in $branches) {
    $name = $branch -replace "^origin/", ""
    if ($name -match $pattern) {
        $largest = [Math]::Max($largest, [int]$Matches[1])
    }
}

$branchName = "{0}-{1:D2}" -f $prefix, ($largest + 1)
& git switch -c $branchName
if ($LASTEXITCODE -ne 0) {
    throw "Git could not create branch $branchName."
}
Write-Host "Created branch: $branchName"
