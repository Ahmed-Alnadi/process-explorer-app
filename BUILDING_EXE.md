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

## Notes

- The build now embeds the app icon and Windows version info automatically.
- The runtime window also uses the packaged app icon.
- `--onefile` starts a little slower because PyInstaller unpacks files at launch.
- The normal folder build is usually faster and more reliable for PySide6 apps.
- After building, you can create a shortcut to the `.exe` and pin it to Start or the taskbar.
