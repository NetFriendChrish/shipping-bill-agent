param([Parameter(Mandatory=$true)][string]$DocumentPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$word = $null
$doc = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $doc = $word.Documents.Open([IO.Path]::GetFullPath($DocumentPath), $false, $true, $false)
    $doc.Content.Text.Replace("`r", "`n").Replace([string][char]7, '')
    foreach ($table in $doc.Tables) {
        foreach ($row in $table.Rows) {
            $cells = @($row.Cells | ForEach-Object { $_.Range.Text.TrimEnd([char]13, [char]7) })
            $cells -join ' | '
        }
    }
} finally {
    if ($null -ne $doc) { $doc.Close(0); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($doc) }
    if ($null -ne $word) { $word.Quit(); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word) }
}
