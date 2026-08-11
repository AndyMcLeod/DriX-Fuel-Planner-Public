<#
.SYNOPSIS
  Populate the contents-page field in the topic reference and save the result
  into the file, so the entries are visible in renderers that do not compute
  Word fields.

.DESCRIPTION
  build_topic_doc.js emits the TOC as an empty field; Word fills it on open.
  That leaves the contents page blank anywhere Word is not doing the work:
  preview panes, LibreOffice, Google Docs, anything reading the XML. This step
  opens the document once through Word, updates the field, and saves the
  computed entries back as the field's cached result.

  Run it after every build; a rebuild wipes the cache again:

      node tools/build_topic_doc.js
      powershell -File tools/bake_toc.ps1

  Requires Windows + desktop Word (COM). There is no cross-platform equivalent
  here: LibreOffice could stand in, but it is not installed on this machine.

  Word rewrites the whole package on save, so the committed .docx is no longer
  byte-for-byte the generator's output, and two bakes of identical content will
  not produce identical bytes (Word stamps revision ids). Reproducible in
  content, not in bytes.

  ASCII only, deliberately: Windows PowerShell 5.1 reads a UTF-8 file without a
  BOM as ANSI, and a stray em dash in a string is a parse error.
#>
param(
  # The document lives outside this repo; keep in step with OUT in
  # build_topic_doc.js.
  [string]$Path = "D:\Claude\ROS2\DriX8_ROS2_Topic_Reference.docx"
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Path)) {
  throw "$Path not found. Run 'node tools/build_topic_doc.js' first."
}
$full = (Resolve-Path $Path).Path

# An open copy in Word blocks the save and would silently bake nothing.
if (Get-Process WINWORD -ErrorAction SilentlyContinue) {
  try {
    $running = [Runtime.InteropServices.Marshal]::GetActiveObject("Word.Application")
    foreach ($d in @($running.Documents)) {
      if ($d.FullName -eq $full) {
        Write-Host "closing the document already open in Word"
        $d.Close($false)
      }
    }
  } catch {
    throw "Word is running but not reachable over COM. Close the document and retry."
  }
}

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
  $doc = $word.Documents.Open($full, $false, $false)   # ReadOnly = false: we save
  $doc.Fields.Update() | Out-Null
  foreach ($toc in $doc.TablesOfContents) { $toc.Update() | Out-Null }
  $doc.Repaginate()

  $entries = 0
  if ($doc.TablesOfContents.Count -gt 0) {
    $entries = @($doc.TablesOfContents.Item(1).Range.Paragraphs |
                 Where-Object { $_.Range.Text.Trim() -ne "" }).Count
  }
  $pages = $doc.ComputeStatistics(2)

  if ($entries -eq 0) {
    throw "the contents field produced no entries. Is the TOC field still in the document?"
  }

  $doc.Save()
  $doc.Close($false)
  Write-Host "baked $entries contents entries across $pages pages into $full"
} finally {
  $word.Quit()
}
