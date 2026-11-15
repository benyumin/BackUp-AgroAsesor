# Descarga a tu proyecto las librerías que la página usa (íconos, mapa y fuentes; los PDF se generan en el servidor), para que NUNCA dependa de internet
# ni de CDNs al dibujar botones, menús y mapas. Se ejecuta UNA vez (y de nuevo solo si cambian las versiones).
# Uso (PowerShell, dentro de la carpeta Agroasesor_Hugo):   .\descargar-librerias.ps1
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$destino = Join-Path $PSScriptRoot 'core\static\vendor'
New-Item -ItemType Directory -Force -Path $destino | Out-Null
$ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
$ok = 0; $fallas = @()

function Bajar($url, $ruta) {
    $archivo = Join-Path $destino $ruta
    New-Item -ItemType Directory -Force -Path (Split-Path $archivo) | Out-Null
    try {
        Invoke-WebRequest -Uri $url -OutFile $archivo -UserAgent $ua -UseBasicParsing
        $bytes = (Get-Item $archivo).Length
        if ($bytes -lt 200) { throw "archivo demasiado pequeño ($bytes bytes)" }
        $hash = (Get-FileHash $archivo -Algorithm SHA256).Hash.Substring(0, 12)
        Write-Host ("  OK   {0,-44} {1,8} bytes  sha256 {2}..." -f $ruta, $bytes, $hash)
        $script:ok++
    } catch {
        Write-Host ("  FALLA {0}: {1}" -f $ruta, $_.Exception.Message) -ForegroundColor Red
        $script:fallas += $ruta
    }
}

Write-Host 'Íconos (lucide 0.468.0)'
Bajar 'https://unpkg.com/lucide@0.468.0/dist/umd/lucide.min.js' 'lucide.min.js'
Write-Host 'Mapa (leaflet 1.9.4 y leaflet-geoman 2.18.0)'
Bajar 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css' 'leaflet\leaflet.css'
Bajar 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js' 'leaflet\leaflet.js'
foreach ($img in 'marker-icon.png', 'marker-icon-2x.png', 'marker-shadow.png', 'layers.png', 'layers-2x.png') {
    Bajar "https://unpkg.com/leaflet@1.9.4/dist/images/$img" "leaflet\images\$img"
}
Bajar 'https://unpkg.com/@geoman-io/leaflet-geoman-free@2.18.0/dist/leaflet-geoman.css' 'leaflet\leaflet-geoman.css'
Bajar 'https://unpkg.com/@geoman-io/leaflet-geoman-free@2.18.0/dist/leaflet-geoman.min.js' 'leaflet\leaflet-geoman.min.js'

Write-Host 'Fuentes (Source Sans 3 e Inter)'
$fuentes = @{
    'source-sans-3' = 'https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;500;600;700&display=swap'
    'inter'         = 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap'
}
foreach ($nombre in $fuentes.Keys) {
    try {
        $css = (Invoke-WebRequest -Uri $fuentes[$nombre] -UserAgent $ua -UseBasicParsing).Content
        $n = 0
        foreach ($m in [regex]::Matches($css, 'url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)')) {
            $n++
            $local = "$nombre-$n.woff2"
            Bajar $m.Groups[1].Value "fonts\$local"
            $css = $css.Replace($m.Groups[1].Value, $local)
        }
        if ($n -eq 0) { throw 'no se encontraron archivos de fuente en la hoja de estilos' }
        [IO.File]::WriteAllText((Join-Path $destino "fonts\$nombre.css"), $css, (New-Object Text.UTF8Encoding($false)))
        Write-Host "  OK   fonts\$nombre.css ($n archivos de fuente)"
        $script:ok++
    } catch {
        Write-Host "  FALLA fuente ${nombre}: $($_.Exception.Message)" -ForegroundColor Red
        $script:fallas += "fonts\$nombre.css"
    }
}

Write-Host ''
if ($fallas.Count -eq 0) {
    Write-Host "Listo: $ok archivos en core\static\vendor. Reinicia con:  docker compose up -d --build" -ForegroundColor Green
} else {
    Write-Host "Terminó con $($fallas.Count) falla(s): $($fallas -join ', ')" -ForegroundColor Yellow
    Write-Host 'Lo que falló seguirá cargándose desde internet como antes (no se rompe nada). Revisa tu conexión y vuelve a ejecutar.'
}
