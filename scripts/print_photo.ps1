param(
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][string]$PrinterName,
    [ValidateRange(1, 10)][int]$Copies = 1,
    [ValidateSet("full4r", "strip2up", "strip1left")][string]$FitMode = "strip1left"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

function Draw-FullImage {
    param(
        [System.Drawing.Graphics]$Graphics,
        [System.Drawing.Image]$Source,
        [System.Drawing.Rectangle]$Bounds
    )
    # Rectangle (integer) selects the unambiguous DrawImage overload used by
    # Windows PowerShell 5.1 and avoids the RectangleF conversion failure.
    $Graphics.DrawImage($Source, $Bounds)
}

function Draw-ImageContain {
    param(
        [System.Drawing.Graphics]$Graphics,
        [System.Drawing.Image]$Source,
        [System.Drawing.Rectangle]$Bounds
    )

    [double]$scale = [Math]::Min($Bounds.Width / [double]$Source.Width, $Bounds.Height / [double]$Source.Height)
    [int]$targetWidth = [int][Math]::Round($Source.Width * $scale)
    [int]$targetHeight = [int][Math]::Round($Source.Height * $scale)
    [int]$targetX = $Bounds.X + [int][Math]::Round(($Bounds.Width - $targetWidth) / 2.0)
    [int]$targetY = $Bounds.Y + [int][Math]::Round(($Bounds.Height - $targetHeight) / 2.0)
    $target = New-Object System.Drawing.Rectangle -ArgumentList $targetX, $targetY, $targetWidth, $targetHeight
    Draw-FullImage -Graphics $Graphics -Source $Source -Bounds $target
}

function Assert-FourBySixMedia {
    param(
        [int]$PixelWidth,
        [int]$PixelHeight,
        [double]$DpiX,
        [double]$DpiY
    )

    [double]$inchW = $PixelWidth / [Math]::Max(1.0, $DpiX)
    [double]$inchH = $PixelHeight / [Math]::Max(1.0, $DpiY)
    [double]$shortSide = [Math]::Min($inchW, $inchH)
    [double]$longSide = [Math]::Max($inchW, $inchH)
    if ([Math]::Abs($shortSide - 4.0) -gt 0.35 -or [Math]::Abs($longSide - 6.0) -gt 0.35) {
        throw "Mode 1 strip membutuhkan media 4x6. Pilih Paper Size (6x4) di Printing Preferences."
    }
}

function Draw-StripOneLeft {
    param(
        [System.Drawing.Graphics]$Graphics,
        [System.Drawing.Image]$Source,
        [System.Drawing.Rectangle]$Bounds
    )

    $Graphics.FillRectangle([System.Drawing.Brushes]::White, $Bounds)
    if ($Bounds.Height -ge $Bounds.Width) {
        [int]$leftWidth = [int][Math]::Floor($Bounds.Width / 2.0)
        $target = New-Object System.Drawing.Rectangle -ArgumentList $Bounds.X, $Bounds.Y, $leftWidth, $Bounds.Height
    } else {
        [int]$topHeight = [int][Math]::Floor($Bounds.Height / 2.0)
        $target = New-Object System.Drawing.Rectangle -ArgumentList $Bounds.X, $Bounds.Y, $Bounds.Width, $topHeight
    }

    $working = $Source.Clone()
    try {
        $sourceIsPortrait = $working.Height -ge $working.Width
        $targetIsPortrait = $target.Height -ge $target.Width
        if ($sourceIsPortrait -ne $targetIsPortrait) {
            $working.RotateFlip([System.Drawing.RotateFlipType]::Rotate90FlipNone)
        }
        Draw-ImageContain -Graphics $Graphics -Source $working -Bounds $target
    }
    finally {
        $working.Dispose()
    }
}

function Draw-StripTwoUp {
    param(
        [System.Drawing.Graphics]$Graphics,
        [System.Drawing.Image]$Source,
        [System.Drawing.Rectangle]$Bounds
    )

    $Graphics.FillRectangle([System.Drawing.Brushes]::White, $Bounds)
    if ($Bounds.Height -ge $Bounds.Width) {
        [int]$firstWidth = [int][Math]::Floor($Bounds.Width / 2.0)
        $first = New-Object System.Drawing.Rectangle -ArgumentList $Bounds.X, $Bounds.Y, $firstWidth, $Bounds.Height
        $second = New-Object System.Drawing.Rectangle -ArgumentList ($Bounds.X + $firstWidth), $Bounds.Y, ($Bounds.Width - $firstWidth), $Bounds.Height
        $targetIsPortrait = $true
    } else {
        [int]$firstHeight = [int][Math]::Floor($Bounds.Height / 2.0)
        $first = New-Object System.Drawing.Rectangle -ArgumentList $Bounds.X, $Bounds.Y, $Bounds.Width, $firstHeight
        $second = New-Object System.Drawing.Rectangle -ArgumentList $Bounds.X, ($Bounds.Y + $firstHeight), $Bounds.Width, ($Bounds.Height - $firstHeight)
        $targetIsPortrait = $false
    }

    $working = $Source.Clone()
    try {
        $sourceIsPortrait = $working.Height -ge $working.Width
        if ($sourceIsPortrait -ne $targetIsPortrait) {
            $working.RotateFlip([System.Drawing.RotateFlipType]::Rotate90FlipNone)
        }
        Draw-FullImage -Graphics $Graphics -Source $working -Bounds $first
        Draw-FullImage -Graphics $Graphics -Source $working -Bounds $second
    }
    finally {
        $working.Dispose()
    }
}

if (-not (Test-Path -LiteralPath $ImagePath -PathType Leaf)) {
    throw "File foto tidak ditemukan: $ImagePath"
}

$document = New-Object System.Drawing.Printing.PrintDocument
$image = $null
$handler = $null

try {
    $document.PrinterSettings.PrinterName = $PrinterName
    if (-not $document.PrinterSettings.IsValid) {
        throw "Printer Windows tidak valid atau tidak tersedia: $PrinterName"
    }

    $document.PrinterSettings.Copies = [int16]$Copies
    $document.DocumentName = "RecordCountdown - " + [IO.Path]::GetFileNameWithoutExtension($ImagePath)
    $document.PrintController = New-Object System.Drawing.Printing.StandardPrintController
    $document.DefaultPageSettings.Margins = New-Object System.Drawing.Printing.Margins -ArgumentList 0, 0, 0, 0
    $image = [System.Drawing.Image]::FromFile($ImagePath)

    $script:paperWidth = 0.0
    $script:paperHeight = 0.0
    $script:dpiX = 0.0
    $script:dpiY = 0.0
    $script:resolvedMode = $FitMode
    $handler = [System.Drawing.Printing.PrintPageEventHandler] {
        param($sender, $eventArgs)

        # PrintableArea/HardMargin are in 1/100 inch, while PageUnit may be
        # Display units. Explicit conversion to device pixels prevents the
        # partial-page output seen with the DS-RX1 driver.
        $graphics = $eventArgs.Graphics
        [double]$dpiX = $graphics.DpiX
        [double]$dpiY = $graphics.DpiY
        $printable = $eventArgs.PageSettings.PrintableArea
        [int]$pixelX = [int][Math]::Round([double]$printable.X * $dpiX / 100.0)
        [int]$pixelY = [int][Math]::Round([double]$printable.Y * $dpiY / 100.0)
        [int]$pixelWidth = [int][Math]::Round([double]$printable.Width * $dpiX / 100.0)
        [int]$pixelHeight = [int][Math]::Round([double]$printable.Height * $dpiY / 100.0)
        if ($pixelWidth -le 0 -or $pixelHeight -le 0) {
            throw "Driver printer tidak mengembalikan area cetak yang valid."
        }

        $script:paperWidth = [double]$pixelWidth
        $script:paperHeight = [double]$pixelHeight
        $script:dpiX = $dpiX
        $script:dpiY = $dpiY

        $graphics.PageUnit = [System.Drawing.GraphicsUnit]::Pixel
        $graphics.PageScale = 1.0
        $graphics.ResetTransform()
        [single]$hardMarginXPx = [single]([double]$eventArgs.PageSettings.HardMarginX * $dpiX / 100.0)
        [single]$hardMarginYPx = [single]([double]$eventArgs.PageSettings.HardMarginY * $dpiY / 100.0)
        $graphics.TranslateTransform(-$hardMarginXPx, -$hardMarginYPx)
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality

        $bounds = New-Object System.Drawing.Rectangle -ArgumentList $pixelX, $pixelY, $pixelWidth, $pixelHeight
        $graphics.SetClip($bounds)

        $resolved = $FitMode
        $script:resolvedMode = $resolved

        if ($resolved -eq "strip1left") {
            Assert-FourBySixMedia -PixelWidth $pixelWidth -PixelHeight $pixelHeight -DpiX $dpiX -DpiY $dpiY
            Draw-StripOneLeft -Graphics $graphics -Source $image -Bounds $bounds
        } elseif ($resolved -eq "strip2up") {
            Draw-StripTwoUp -Graphics $graphics -Source $image -Bounds $bounds
        } else {
            $working = $image.Clone()
            try {
                $sourceIsPortrait = $working.Height -ge $working.Width
                $mediaIsPortrait = $bounds.Height -ge $bounds.Width
                if ($sourceIsPortrait -ne $mediaIsPortrait) {
                    $working.RotateFlip([System.Drawing.RotateFlipType]::Rotate90FlipNone)
                }
                Draw-FullImage -Graphics $graphics -Source $working -Bounds $bounds
            }
            finally {
                $working.Dispose()
            }
        }
        $eventArgs.HasMorePages = $false
    }

    $document.add_PrintPage($handler)
    $document.Print()

    [pscustomobject]@{
        ok = $true
        printer = $PrinterName
        copies = $Copies
        fitMode = $FitMode
        resolvedMode = $script:resolvedMode
        printableWidthPx = $script:paperWidth
        printableHeightPx = $script:paperHeight
        dpiX = $script:dpiX
        dpiY = $script:dpiY
    } | ConvertTo-Json -Compress
}
finally {
    if ($null -ne $handler) {
        $document.remove_PrintPage($handler)
    }
    if ($null -ne $image) {
        $image.Dispose()
    }
    $document.Dispose()
}
