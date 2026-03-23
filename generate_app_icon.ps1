param(
    [string]$OutputPath = (Join-Path $PSScriptRoot "assets\task_manager_icon.ico")
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing

function New-RoundedRectPath {
    param(
        [float]$X,
        [float]$Y,
        [float]$Width,
        [float]$Height,
        [float]$Radius
    )

    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $diameter = $Radius * 2
    $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)
    $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)
    $path.AddArc($X + $Width - $diameter, $Y + $Height - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

$pngOutputPath = [System.IO.Path]::ChangeExtension($OutputPath, ".png")
$size = 256
$bitmap = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$graphics.Clear([System.Drawing.Color]::Transparent)

try {
    $baseRect = New-Object System.Drawing.RectangleF(18, 18, 220, 220)
    $basePath = New-RoundedRectPath -X $baseRect.X -Y $baseRect.Y -Width $baseRect.Width -Height $baseRect.Height -Radius 46

    $baseBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        $baseRect,
        [System.Drawing.Color]::FromArgb(255, 11, 28, 43),
        [System.Drawing.Color]::FromArgb(255, 28, 107, 124),
        50.0
    )
    $graphics.FillPath($baseBrush, $basePath)

    $highlightBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        (New-Object System.Drawing.RectangleF(28, 24, 200, 90)),
        [System.Drawing.Color]::FromArgb(85, 158, 228, 255),
        [System.Drawing.Color]::FromArgb(0, 158, 228, 255),
        90.0
    )
    $graphics.FillPath($highlightBrush, $basePath)

    $borderPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(130, 186, 239, 255), 4)
    $graphics.DrawPath($borderPen, $basePath)

    $screenRect = New-Object System.Drawing.RectangleF(42, 48, 172, 124)
    $screenPath = New-RoundedRectPath -X $screenRect.X -Y $screenRect.Y -Width $screenRect.Width -Height $screenRect.Height -Radius 24
    $screenBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        $screenRect,
        [System.Drawing.Color]::FromArgb(240, 8, 18, 30),
        [System.Drawing.Color]::FromArgb(240, 22, 48, 70),
        90.0
    )
    $graphics.FillPath($screenBrush, $screenPath)

    $gridPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(34, 205, 238, 255), 2)
    foreach ($y in 76, 104, 132, 160) {
        $graphics.DrawLine($gridPen, 58, $y, 198, $y)
    }

    $barBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 51, 211, 168))
    $graphics.FillRectangle($barBrush, 68, 124, 18, 36)
    $graphics.FillRectangle($barBrush, 96, 102, 18, 58)
    $graphics.FillRectangle($barBrush, 124, 82, 18, 78)
    $graphics.FillRectangle($barBrush, 152, 64, 18, 96)

    $linePen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 108, 197, 255), 7)
    $linePen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $linePen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $linePen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $graphics.DrawLines(
        $linePen,
        [System.Drawing.Point[]]@(
            (New-Object System.Drawing.Point(62, 146)),
            (New-Object System.Drawing.Point(92, 118)),
            (New-Object System.Drawing.Point(120, 128)),
            (New-Object System.Drawing.Point(150, 92)),
            (New-Object System.Drawing.Point(186, 72))
        )
    )

    $accentBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 255, 188, 79))
    $graphics.FillEllipse($accentBrush, 176, 62, 20, 20)

    $footerBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 231, 244, 255))
    $graphics.FillRectangle($footerBrush, 58, 188, 140, 12)
    $graphics.FillRectangle($footerBrush, 58, 208, 96, 12)

    $bitmap.Save($pngOutputPath, [System.Drawing.Imaging.ImageFormat]::Png)

    $pngStream = New-Object System.IO.MemoryStream
    $bitmap.Save($pngStream, [System.Drawing.Imaging.ImageFormat]::Png)
    $pngBytes = $pngStream.ToArray()

    $fileStream = [System.IO.File]::Open($OutputPath, [System.IO.FileMode]::Create)
    $writer = New-Object System.IO.BinaryWriter($fileStream)
    try {
        $writer.Write([UInt16]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]1)
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]32)
        $writer.Write([UInt32]$pngBytes.Length)
        $writer.Write([UInt32]22)
        $writer.Write($pngBytes)
    }
    finally {
        $writer.Dispose()
        $fileStream.Dispose()
        $pngStream.Dispose()
    }
}
finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
