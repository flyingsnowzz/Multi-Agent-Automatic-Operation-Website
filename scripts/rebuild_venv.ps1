param(
  [string]$Python = "3.12"
)

$ErrorActionPreference = "Stop"

if (Test-Path "venv") {
  Remove-Item -Recurse -Force "venv"
}

py -$Python -m venv venv

& .\venv\Scripts\python.exe -m pip install --upgrade pip
& .\venv\Scripts\python.exe -m pip install -r requirements.txt
& .\venv\Scripts\python.exe -m pip check
& .\venv\Scripts\python.exe -m unittest discover -s tests -v
& .\venv\Scripts\python.exe -m pytest -q tests

