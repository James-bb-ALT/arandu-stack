$ErrorActionPreference = 'Stop'
$root = Join-Path $env:USERPROFILE '.codex\skills'
$names = @('awesome-design-md','brandkit','caveman','design-taste-frontend','design-taste-frontend-v1','full-output-enforcement','gpt-taste','high-end-visual-design','image-to-code','imagegen-frontend-mobile','imagegen-frontend-web','industrial-brutalist-ui','llm-wiki','minimalist-ui','redesign-existing-projects','reverse-skill-router','stitch-design-taste','web-design-guidelines','wigolo','dataviz','artifact-design','artifact-diagramming','update-config','keybindings-help','code-review','simplify','fewer-permission-prompts','loop','claude-api','workflow-authoring','run','init','security-review')
foreach ($name in $names) {
  $file = Join-Path $root "$name\SKILL.md"
  if (-not (Test-Path -LiteralPath $file)) { throw "Missing skill: $name" }
  $text = Get-Content -Raw -LiteralPath $file
  if ($text -notmatch '(?ms)^---\s*\r?\nname:\s*' + [regex]::Escape($name) + '\s*\r?\n') { throw "Invalid frontmatter: $name" }
}
foreach ($doc in @('docs/codex/SKILL-MANIFEST.md','docs/codex/ROUTING.md','docs/codex/OPERATING-GUIDE.md','docs/codex/COMPATIBILITY.md')) {
  if (-not (Test-Path -LiteralPath $doc)) { throw "Missing document: $doc" }
}
Write-Output "PASS: $($names.Count) native skills and 4 repository documents validated."
