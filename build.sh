#!/usr/bin/env bash
# Pocket Adventure FX aus der eigenen Originalkopie bauen – ein Befehl.
#
#   ./build.sh <Kopie> [<weitere Kopie> …]
#
# <Kopie> ist ein Verzeichnis mit den Dateien der VGA-Diskettenversion
# (000.LFL, DISK01.LEC … DISK04.LEC). Jede Sprachfassung, die man angibt,
# wird im Spiel wählbar; die Sprache erkennt das Skript selbst. Beispiel:
#
#   ./build.sh ~/Spiele/MONKEY ~/Spiele/MONKEY-DE
#
# Ergebnis: dist/PocketAdventureFX.arduboy – ein Paket für Arduboy Toolset,
# Cart-Editoren oder den Emulator Ardens. Es enthält Grafiken und Texte der
# eigenen Kopie und ist nur für den eigenen Gebrauch.
#
# Braucht Python 3.10+ und curl. arduino-cli und den Arduboy-Kern richtet das
# Skript beim ersten Lauf in build/arduino ein; am System ändert es nichts.
set -euo pipefail
cd "$(dirname "$0")"

ARDUINO_CLI_VERSION=1.5.1
ARDUBOY_CORE=arduboy-homemade:avr@1.4.1
# Der Arduboy-Kern hat keine eigenen Werkzeuge; Compiler und Bibliotheken
# kommen aus dem AVR-Kern von Arduino.
AVR_CORE=arduino:avr@1.8.8
ARDUBOY_INDEX=https://raw.githubusercontent.com/MrBlinky/Arduboy-homemade-package/master/package_arduboy_homemade_index.json

die() { echo "build.sh: $*" >&2; exit 1; }

[ $# -ge 1 ] || die "Aufruf: ./build.sh <Kopie> [<weitere Kopie> …]  (Verzeichnis mit DISK01.LEC)"
copies=()
for dir in "$@"; do
  [ -f "$dir/000.LFL" ] && [ -f "$dir/DISK01.LEC" ] || die "$dir: keine Originalkopie (000.LFL und DISK01.LEC fehlen)"
  copies+=("$(cd "$dir" && pwd)")
done

command -v python3 >/dev/null || die "python3 fehlt"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python 3.10 oder neuer nötig"
command -v curl >/dev/null || die "curl fehlt"

# arduino-cli lokal, mit eigener Konfiguration und eigenem Datenverzeichnis
ARDUINO_DIR="$PWD/build/arduino"
CLI="$ARDUINO_DIR/arduino-cli"
CONFIG="$ARDUINO_DIR/arduino-cli.yaml"
if [ ! -x "$CLI" ]; then
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) platform=macOS_ARM64 ;;
    Darwin-x86_64) platform=macOS_64bit ;;
    Linux-x86_64) platform=Linux_64bit ;;
    Linux-aarch64 | Linux-arm64) platform=Linux_ARM64 ;;
    *) die "kein arduino-cli für $(uname -s)-$(uname -m); bitte selbst installieren und ARDUINO_CLI setzen" ;;
  esac
  echo "build.sh: lade arduino-cli $ARDUINO_CLI_VERSION ($platform)"
  mkdir -p "$ARDUINO_DIR"
  curl -fsSL "https://downloads.arduino.cc/arduino-cli/arduino-cli_${ARDUINO_CLI_VERSION}_${platform}.tar.gz" \
    | tar -xz -C "$ARDUINO_DIR" arduino-cli
fi
if [ ! -f "$CONFIG" ]; then
  cat > "$CONFIG" <<EOF
board_manager:
  additional_urls:
    - $ARDUBOY_INDEX
directories:
  data: $ARDUINO_DIR/data
  downloads: $ARDUINO_DIR/downloads
  user: $ARDUINO_DIR/user
EOF
fi
installed=$("$CLI" --config-file "$CONFIG" core list)
if ! grep -q '^arduboy-homemade:avr' <<<"$installed" || ! grep -q '^arduino:avr' <<<"$installed"; then
  echo "build.sh: richte den Arduboy-Kern ein ($ARDUBOY_CORE, $AVR_CORE)"
  "$CLI" --config-file "$CONFIG" core update-index >/dev/null
  "$CLI" --config-file "$CONFIG" core install "$AVR_CORE" >/dev/null
  "$CLI" --config-file "$CONFIG" core install "$ARDUBOY_CORE" >/dev/null
fi

# Make trennt Listen an Leerzeichen: Verweise mit festen Namen auf die
# Kopien, und relative Pfade (make läuft hier im Projektverzeichnis).
links=build/originals
rm -rf "$links" && mkdir -p "$links"
originals=()
for i in "${!copies[@]}"; do
  ln -s "${copies[$i]}" "$links/$((i + 1))"
  originals+=("$links/$((i + 1))")
done

make package ORIGINALS="${originals[*]}" \
  ARDUINO_CLI="build/arduino/arduino-cli --config-file build/arduino/arduino-cli.yaml"
echo
echo "Fertig: dist/PocketAdventureFX.arduboy"
