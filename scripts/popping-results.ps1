#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('Setup', 'List', 'Download')]
    [string]$Action,

    [Parameter(Mandatory)]
    [ValidatePattern('\A[A-Za-z0-9_-]+\z')]
    [string]$Course,

    [string]$BaseUrl = 'https://popping.onrender.com',
    [string]$CredentialDirectory = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Popping'),

    [ValidateRange(1, [int]::MaxValue)]
    [int]$Week,
    [string]$OutputDirectory = '.',

    [securestring]$Token,
    [switch]$Replace
)

$ErrorActionPreference = 'Stop'

if ($env:OS -ne 'Windows_NT') {
    throw 'Run this client in PowerShell on Windows to use Windows credential encryption.'
}
if ($Action -ne 'Setup' -and ($PSBoundParameters.ContainsKey('Token') -or $Replace)) {
    throw 'Use -Token and -Replace with -Action Setup.'
}
if ($Action -eq 'Download' -and -not $PSBoundParameters.ContainsKey('Week')) {
    throw 'Specify the lecture week with -Week N.'
}

$baseUri = $null
if (-not [uri]::TryCreate($BaseUrl, [UriKind]::Absolute, [ref]$baseUri) -or
    $baseUri.AbsolutePath -ne '/' -or $baseUri.UserInfo -or $baseUri.Query -or $baseUri.Fragment) {
    throw 'Use a server origin for -BaseUrl, such as https://popping.onrender.com or http://127.0.0.1:5000.'
}
$origin = $baseUri.GetLeftPart([UriPartial]::Authority)
$isLoopback = $baseUri.DnsSafeHost -in 'localhost', '127.0.0.1', '::1' -and
    $baseUri.Scheme -in 'http', 'https'
if ($origin -ne 'https://popping.onrender.com' -and -not $isLoopback) {
    throw 'Use the Popping production origin or a loopback origin for -BaseUrl.'
}

$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $key = $sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes("$origin`n$Course"))
    $credentialName = 'results-' + [BitConverter]::ToString($key).Replace('-', '').ToLowerInvariant() + '.clixml'
} finally {
    $sha256.Dispose()
}
$credentialRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($CredentialDirectory)
$credentialPath = Join-Path $credentialRoot $credentialName

if ($Action -eq 'Setup') {
    if ([IO.File]::Exists($credentialPath) -and -not $Replace) {
        throw 'A local token already exists for this course and server. Run Setup with -Replace to update it.'
    }
    if ($null -eq $Token) {
        $Token = Read-Host 'Paste the token from Tools > Request API Token' -AsSecureString
    }
} else {
    if (-not [IO.File]::Exists($credentialPath)) {
        throw "Run Setup for '$Course' at $origin with the token from Tools > Request API Token."
    }
    try {
        $Token = Import-Clixml -LiteralPath $credentialPath
    } catch {
        throw 'Windows could not read the local token. Run Setup with -Replace under this Windows account.'
    }
}
if ($Token -isnot [securestring] -or $Token.Length -eq 0) {
    throw 'Provide a token from Tools > Request API Token.'
}

$tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Token)
try {
    $tokenText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    if ($tokenText -cnotmatch '\Apopres_[A-Za-z0-9_-]{43}\z') {
        throw 'Paste the complete token from Tools > Request API Token.'
    }
    if ($Action -ne 'Setup') {
        Add-Type -AssemblyName System.Net.Http
        $authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $tokenText)
    }
} finally {
    $tokenText = $null
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
}

if ($Action -eq 'Setup') {
    $null = [IO.Directory]::CreateDirectory($credentialRoot)
    $tempCredentialPath = Join-Path $credentialRoot ([guid]::NewGuid().ToString('N') + '.tmp')
    try {
        Export-Clixml -InputObject $Token -LiteralPath $tempCredentialPath
        if ($Replace -and [IO.File]::Exists($credentialPath)) {
            [IO.File]::Replace($tempCredentialPath, $credentialPath, [NullString]::Value)
        } else {
            [IO.File]::Move($tempCredentialPath, $credentialPath)
        }
    } finally {
        [IO.File]::Delete($tempCredentialPath)
    }
    Write-Output "Saved the local token for '$Course' at $origin."
    return
}

$url = "$origin/api/courses/$Course/results/weeks"
$completion = [Net.Http.HttpCompletionOption]::ResponseContentRead
if ($Action -eq 'Download') {
    $url += "/$Week/download"
    $completion = [Net.Http.HttpCompletionOption]::ResponseHeadersRead
}
$handler = [Net.Http.HttpClientHandler]::new()
$handler.AllowAutoRedirect = $false
$handler.UseCookies = $false
if ($isLoopback) {
    $handler.UseProxy = $false
}
$client = [Net.Http.HttpClient]::new($handler)
$client.DefaultRequestHeaders.Authorization = $authorization
$timeout = [Threading.CancellationTokenSource]::new([TimeSpan]::FromMinutes(2))
$response = $null
try {
    try {
        $response = $client.GetAsync($url, $completion, $timeout.Token).GetAwaiter().GetResult()
    } catch {
        throw "Could not complete the request to $origin. Check the connection and retry."
    }
    $status = [int]$response.StatusCode
    switch ($status) {
        200 { break }
        401 { throw 'Popping rejected the token. Check Tools > Request API Token and run Setup with -Replace as needed.' }
        403 { throw "Popping denied the results request for '$Course' (HTTP 403)." }
        404 { throw 'Popping could not find the requested course, lecture week, or endpoint (HTTP 404).' }
        409 { throw 'Popping could not export this lecture week at present (HTTP 409). Try again after class activity ends.' }
        default { throw "Popping at $origin returned HTTP $status for the results request." }
    }

    if ($Action -eq 'List') {
        $json = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        try {
            $results = ConvertFrom-Json -InputObject $json
        } catch {
            throw 'Expected JSON week data from Popping.'
        }
        if ($results.course -cne $Course -or $results.weeks -isnot [array]) {
            throw "Expected week data for '$Course' from Popping."
        }
        Write-Output $json
        return
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $outputRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
    $null = [IO.Directory]::CreateDirectory($outputRoot)
    $zipPath = Join-Path $outputRoot "popping_${Course}_week_${Week}_export.zip"
    $tempZipPath = Join-Path $outputRoot ([guid]::NewGuid().ToString('N') + '.tmp')
    try {
        $file = [IO.File]::Open($tempZipPath, [IO.FileMode]::CreateNew)
        try {
            $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
            $null = $stream.CopyToAsync($file, 81920, $timeout.Token).GetAwaiter().GetResult()
            $contentLength = $response.Content.Headers.ContentLength
            if ($null -ne $contentLength -and $file.Length -ne $contentLength) {
                throw 'Incomplete ZIP download.'
            }
        } catch {
            throw 'Could not finish saving the ZIP. Check the connection and output directory, then retry.'
        } finally {
            $file.Dispose()
        }

        try {
            $zip = [IO.Compression.ZipFile]::OpenRead($tempZipPath)
            try {
                foreach ($name in 'course_data.xlsx', 'manifest.json') {
                    $entry = $zip.GetEntry($name)
                    if ($null -eq $entry -or $entry.Length -eq 0) {
                        throw "Missing ZIP entry: $name"
                    }
                }
                $buffer = [byte[]]::new(81920)
                foreach ($entry in $zip.Entries) {
                    $member = $entry.Open()
                    try {
                        $length = 0L
                        while (($read = $member.Read($buffer, 0, $buffer.Length)) -gt 0) {
                            $length += $read
                        }
                        if ($length -ne $entry.Length) {
                            throw 'Incomplete ZIP entry.'
                        }
                    } finally {
                        $member.Dispose()
                    }
                }
            } finally {
                $zip.Dispose()
            }
        } catch {
            throw 'Expected a complete Popping ZIP containing course_data.xlsx and manifest.json. Retry the results download.'
        }

        if ([IO.File]::Exists($zipPath)) {
            [IO.File]::Replace($tempZipPath, $zipPath, [NullString]::Value)
        } else {
            [IO.File]::Move($tempZipPath, $zipPath)
        }
        Write-Output $zipPath
    } finally {
        [IO.File]::Delete($tempZipPath)
    }
} finally {
    if ($null -ne $response) {
        $response.Dispose()
    }
    $timeout.Dispose()
    $client.Dispose()
}
