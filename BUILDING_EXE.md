# Build The App As A Windows EXE

This app can be packaged with PyInstaller.

## 1. Install PyInstaller

Run this once:

```powershell
& "C:\Users\Nadzilla\AppData\Local\Programs\Python\Python314\python.exe" -m pip install pyinstaller
```

## 2. Build It

From this folder:

```powershell
cd C:\Users\Nadzilla\Downloads\HARDRIVE\apps
.\build_exe.ps1
```

That creates a normal Windows app bundle at:

```text
dist\TaskManagerClone\TaskManagerClone.exe
```

## 3. Optional Single-File EXE

If you want one `.exe` instead of a folder:

```powershell
.\build_exe.ps1 -OneFile
```

That creates:

```text
dist\TaskManagerClone.exe
```

## 4. Optional Free Self-Signing

If you want a free local code-signing pass, you can self-sign the built app:

```powershell
.\build_exe.ps1 -SelfSign
```

If you also want the self-signed certificate trusted on your own Windows account:

```powershell
.\build_exe.ps1 -SelfSign -TrustSelfSigned
```

Important:
- this is a self-signed certificate, not a publicly trusted commercial code-signing certificate
- it is free, but other machines will still treat it as untrusted unless they trust that certificate too

## Notes

- The build now embeds the app icon and Windows version info automatically.
- The built `.exe` now uses a custom Windows manifest with:
  - administrator launch requirement
  - per-monitor DPI awareness
  - long-path awareness
- That is intentional for better process and service coverage.
- The runtime window also uses the packaged app icon.
- `--onefile` starts a little slower because PyInstaller unpacks files at launch.
- The normal folder build is usually faster and more reliable for PySide6 apps.
- `Set-AuthenticodeSignature` is used for the optional free self-sign step, so no paid certificate is required for that local-only signing mode.
- After building, you can create a shortcut to the `.exe` and pin it to Start or the taskbar.
