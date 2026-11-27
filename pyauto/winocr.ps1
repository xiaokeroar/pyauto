<#
.SYNOPSIS
  Windows.Media.Ocr worker: recognises text in an image and returns JSON.

.DESCRIPTION
  Uses the OCR engine built into Windows 10/11 (no third-party binaries, no
  install, works offline). Called by pyauto/ocr.py; also usable standalone.

  JSON goes to stdout as raw UTF-8 bytes with a "##PYAUTO##" prefix, or to a
  file when -Out is given. The image is read with System.IO and handed to the
  decoder as an in-memory stream: Windows.Storage.StorageFile is avoided on
  purpose, because its broker refuses Documents/Desktop paths for unpackaged
  desktop processes ("access denied ... not in a location the app can access").

  NOTE: keep this file ASCII-only. PowerShell 5.1 reads .ps1 as ANSI unless it
  has a BOM, so non-ASCII characters can corrupt the script when it is parsed.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File winocr.ps1 -Image C:\a.png -Lang zh-Hans-CN

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File winocr.ps1 -ListLangs
#>
param(
    [string]$Image = '',
    [string]$Lang = '',
    [string]$Out = '',
    [switch]$ListLangs
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Json($obj, $path) {
    $json = $obj | ConvertTo-Json -Depth 8 -Compress
    $utf8 = [System.Text.Encoding]::UTF8
    if ($path) {
        [System.IO.File]::WriteAllBytes($path, $utf8.GetBytes($json))
    }
    else {
        $bytes = $utf8.GetBytes("##PYAUTO##" + $json)
        $stdout = [Console]::OpenStandardOutput()
        $stdout.Write($bytes, 0, $bytes.Length)
        $stdout.Flush()
    }
}

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics, ContentType = WindowsRuntime]

if ($ListLangs) {
    $langs = @()
    foreach ($l in [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages) {
        $langs += [ordered]@{ tag = $l.LanguageTag; name = $l.DisplayName }
    }
    Write-Json ([ordered]@{
            ok            = $true
            langs         = $langs
            max_dimension = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
        }) $Out
    return
}

if (-not $Image) { throw 'Image parameter is required' }
if (-not (Test-Path -LiteralPath $Image)) { throw "image not found: $Image" }

$result = [ordered]@{
    ok         = $false
    engine     = 'windows-ocr'
    lang       = ''
    text       = ''
    lines      = @()
    error      = ''
    image      = $Image
    width      = 0
    height     = 0
    elapsed_ms = 0
}

# WinRT IAsyncOperation<T> -> .NET Task<T>
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

function Await($op, $type) {
    $m = $asTaskGeneric.MakeGenericMethod($type)
    $t = $m.Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$bitmap = $null
try {
    $full = (Resolve-Path -LiteralPath $Image).Path
    $bytes = [System.IO.File]::ReadAllBytes($full)

    $ms = New-Object System.IO.MemoryStream
    $ms.Write($bytes, 0, $bytes.Length)
    $ms.Position = 0
    $ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($ms)

    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $result.width = [int]$decoder.PixelWidth
    $result.height = [int]$decoder.PixelHeight

    $maxDim = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    if ($result.width -gt $maxDim -or $result.height -gt $maxDim) {
        throw "image is $($result.width)x$($result.height); Windows OCR supports at most ${maxDim}px per side (downscale with --scale)"
    }

    $bitmap = Await ($decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])

    $engine = $null
    if ($Lang) {
        try {
            $language = [Windows.Globalization.Language]::new($Lang)
            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        }
        catch { $engine = $null }
    }
    if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
    if (-not $engine) { throw "no OCR engine for language '$Lang' and no user-profile language available" }

    $result.lang = $engine.RecognizerLanguage.LanguageTag
    $ocr = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

    $lines = New-Object System.Collections.ArrayList
    foreach ($line in $ocr.Lines) {
        $words = New-Object System.Collections.ArrayList
        $minX = [double]::MaxValue; $minY = [double]::MaxValue
        $maxX = 0.0; $maxY = 0.0
        foreach ($w in $line.Words) {
            $r = $w.BoundingRect
            $words.Add([ordered]@{
                    t = $w.Text
                    x = [math]::Round($r.X, 1)
                    y = [math]::Round($r.Y, 1)
                    w = [math]::Round($r.Width, 1)
                    h = [math]::Round($r.Height, 1)
                }) | Out-Null
            if ($r.X -lt $minX) { $minX = $r.X }
            if ($r.Y -lt $minY) { $minY = $r.Y }
            if (($r.X + $r.Width) -gt $maxX) { $maxX = $r.X + $r.Width }
            if (($r.Y + $r.Height) -gt $maxY) { $maxY = $r.Y + $r.Height }
        }
        if ($words.Count -eq 0) { continue }
        $lines.Add([ordered]@{
                text  = $line.Text
                x     = [math]::Round($minX, 1)
                y     = [math]::Round($minY, 1)
                w     = [math]::Round($maxX - $minX, 1)
                h     = [math]::Round($maxY - $minY, 1)
                words = $words.ToArray()
            }) | Out-Null
    }

    $result.lines = @($lines.ToArray())
    $result.text = (($lines | ForEach-Object { $_.text }) -join "`n")
    $result.ok = $true
}
catch {
    $result.ok = $false
    $result.error = $_.Exception.Message
    if ($_.Exception.InnerException) { $result.error += ' | ' + $_.Exception.InnerException.Message }
}
finally {
    if ($bitmap) { $bitmap.Dispose() }
    $sw.Stop()
    $result.elapsed_ms = [int]$sw.ElapsedMilliseconds
    Write-Json $result $Out
}
