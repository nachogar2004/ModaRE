#!/bin/bash
# Ordenador optimizador como servicio de macOS (launchd): arranca solo al iniciar sesión, sigue en segundo plano
# sin ventana y se reinicia si se cae. Lee la base de datos de code/.env (MODARE_DB=…).
#
#   ./servicio_trabajador.sh instalar      instala y arranca el servicio
#   ./servicio_trabajador.sh estado        ¿está en marcha? + últimas líneas del registro
#   ./servicio_trabajador.sh registro      ver el registro en directo (Ctrl + C para salir)
#   ./servicio_trabajador.sh reiniciar     tras un «git pull», para usar el código nuevo
#   ./servicio_trabajador.sh desinstalar   lo para y lo quita
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ETIQUETA="com.remode.trabajador"
PLIST="$HOME/Library/LaunchAgents/$ETIQUETA.plist"
REGISTRO="$HOME/Library/Logs/remode-trabajador.log"
DOMINIO="gui/$(id -u)"

case "${1:-}" in
  instalar)
    [ "$(uname)" = "Darwin" ] || { echo "Este script es para macOS."; exit 1; }
    [ -x "$DIR/.venv/bin/python" ] || { echo "Falta el entorno: cd $DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
    grep -q '^MODARE_DB=' "$DIR/.env" 2>/dev/null || {
      echo "Falta $DIR/.env con la base de datos. Créalo con una línea así (con tu cadena de Neon):"
      echo "  MODARE_DB=postgresql://usuario:clave@host/neondb?sslmode=require"
      exit 1; }
    chmod 600 "$DIR/.env"                       # solo tu usuario puede leer la contraseña de la base
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$ETIQUETA</string>
  <key>ProgramArguments</key>
  <array><string>$DIR/.venv/bin/python</string><string>$DIR/trabajador.py</string></array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$REGISTRO</string>
  <key>StandardErrorPath</key><string>$REGISTRO</string>
</dict>
</plist>
PLIST
    launchctl bootout "$DOMINIO" "$PLIST" 2>/dev/null || true
    launchctl bootstrap "$DOMINIO" "$PLIST"
    sleep 3
    echo "Servicio instalado y en marcha. Arrancará solo cada vez que inicies sesión."
    tail -n 3 "$REGISTRO" 2>/dev/null || true
    ;;
  estado)
    if launchctl print "$DOMINIO/$ETIQUETA" >/dev/null 2>&1; then
      launchctl print "$DOMINIO/$ETIQUETA" | grep -E "^\s*(state|pid) =" || true
    else
      echo "El servicio no está instalado."
    fi
    echo "--- últimas líneas de $REGISTRO:"; tail -n 8 "$REGISTRO" 2>/dev/null || echo "(sin registro todavía)"
    ;;
  registro)
    tail -n 30 -f "$REGISTRO"
    ;;
  reiniciar)
    launchctl kickstart -k "$DOMINIO/$ETIQUETA" && echo "Servicio reiniciado con el código actual."
    ;;
  desinstalar)
    launchctl bootout "$DOMINIO" "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Servicio parado y desinstalado (el registro sigue en $REGISTRO)."
    ;;
  *)
    sed -n '2,10p' "$0"; exit 1 ;;
esac
