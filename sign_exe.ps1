param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [string]$Subject = "CN=TaskManagerClone Local",
    [switch]$TrustLocally
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Path)) {
    throw "File not found: $Path"
}

$codeSigningEku = "2.5.29.37={text}1.3.6.1.5.5.7.3.3"
$cert = Get-ChildItem Cert:\CurrentUser\My |
    Where-Object { $_.Subject -eq $Subject -and $_.HasPrivateKey } |
    Select-Object -First 1

if (-not $cert) {
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $Subject `
        -FriendlyName "TaskManagerClone Self-Signed" `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -HashAlgorithm SHA256 `
        -KeyAlgorithm RSA `
        -KeyLength 2048 `
        -TextExtension $codeSigningEku
}

$signature = Set-AuthenticodeSignature -FilePath $Path -Certificate $cert
if ($signature.Status -ne "Valid") {
    throw "Signing failed: $($signature.Status) $($signature.StatusMessage)"
}

if ($TrustLocally) {
    $rootStore = New-Object System.Security.Cryptography.X509Certificates.X509Store("Root", "CurrentUser")
    $rootStore.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
    try {
        $rootStore.Add($cert)
    }
    finally {
        $rootStore.Close()
    }
}

Write-Host "Signed:" $Path
if ($TrustLocally) {
    Write-Host "The self-signed certificate was also trusted locally for the current user."
}
