# Contributing

## Branch naming

Every new working branch must use this order:

    <repository>/<feature>/<year>/<month>/<date>/<daily-number>

For this repository:

    overlay-text-studio/<feature>/YYYY/MM/DD/NN

Example:

    overlay-text-studio/caption-export/2026/08/08/01

Rules:

- Separate every section with a forward slash.
- Use lowercase letters, numbers, and hyphens inside repository and feature sections.
- Keep the feature short and descriptive.
- Use four-digit year, two-digit month, and two-digit day.
- The final number starts at 01 each day and increments for every new branch
  created for the same repository and feature.
- The permanent main branch is exempt.

Create the next numbered branch automatically:

    .\scripts\new_branch.ps1 -Feature "caption export"

GitHub validates this format on every pull request.
