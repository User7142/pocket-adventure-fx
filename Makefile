# Pocket Adventure FX – Build
#
# Braucht mindestens eine eigene Kopie der VGA-Diskettenversion (DISK01.LEC …);
# jede weitere Sprachfassung kommt als Sprache ins Spiel:
#   make ORIGINALS="/pfad/zu/MONKEY /pfad/zu/MONKEY-DE"
# oder dauerhaft in config.mk (nicht im Repo):
#   ORIGINALS := /pfad/zu/MONKEY /pfad/zu/MONKEY-DE
# Grafiken, Laufwege und Musik kommen aus der ersten Kopie.
#
#   make            Daten + Sketch bauen
#   make package    dazu das .arduboy-Paket (dist/PocketAdventureFX.arduboy)
#   make data       nur FX-Daten (game.adv → game.bin, gamedata.h, fxdata.h)
#   make art        Platzhalter-Grafiken neu erzeugen (überschreibt nicht)
#   make test       Compiler-Tests und Szenendurchläufe auf dem Host-Prüfstand
#   make fxhost     nur den Host-Prüfstand bauen (build/fxhost)
#   make upload     Sketch + FX-Entwicklungsdaten auf den Arduboy FX
#   make clean

-include config.mk

FQBN        := arduboy-homemade:avr:arduboy-fx
# build.sh setzt hier ein lokal installiertes arduino-cli samt eigener Konfiguration ein.
ARDUINO_CLI ?= arduino-cli
PORT        ?= $(shell ls /dev/cu.usbmodem* 2>/dev/null | head -1)
PYTHON      := .venv/bin/python
SKETCH      := PocketAdventureFX
FXDIR       := $(SKETCH)/fxdata
BUILD       := build

SOURCES     := $(wildcard $(SKETCH)/*.cpp $(SKETCH)/*.h $(SKETCH)/*.ino)
ASSETS      := game/game.adv $(shell find game/art -type f)
# Frühere Variablen (eine Kopie je Sprache) gelten weiter.
ORIGINALS   ?= $(strip $(ORIGINAL) $(ORIGINAL_DE))
ORIGINAL_FILES := $(foreach d,$(ORIGINALS),$(wildcard $(d)/DISK*.LEC))

# Welche Kopien gebaut wurden: Ändert sich die Auswahl (auch hinter den
# Verweisen von build.sh), muss neu übersetzt werden, obwohl keine Datei
# neuer ist. Der Stempel wird nur bei Änderung geschrieben.
ORIGINALS_STAMP := $(BUILD)/originals.txt
ORIGINALS_REAL := $(foreach d,$(ORIGINALS),$(realpath $(d)))
$(shell mkdir -p $(BUILD); echo '$(ORIGINALS_REAL)' | cmp -s - $(ORIGINALS_STAMP) || echo '$(ORIGINALS_REAL)' > $(ORIGINALS_STAMP))

PACKAGE     := dist/PocketAdventureFX.arduboy

.PHONY: all data art test fxhost package upload upload-sketch upload-data clean

all: $(BUILD)/$(SKETCH).ino.hex

$(PYTHON):
	python3 -m venv .venv
	$(PYTHON) -m pip install -q -r tools/requirements.txt

data: $(FXDIR)/fxdata.h

$(FXDIR)/game.bin $(SKETCH)/gamedata.h &: $(ASSETS) $(ORIGINAL_FILES) $(ORIGINALS_STAMP) $(wildcard tools/*.py) | $(PYTHON)
	@test -n "$(ORIGINALS)" || (echo "ORIGINALS missing: make ORIGINALS=/path/to/MONKEY (or set it in config.mk)"; exit 1)
	$(PYTHON) tools/advc.py game/game.adv $(foreach d,$(ORIGINALS),--original "$(d)") --preview $(BUILD)/preview \
	  --bin $(FXDIR)/game.bin --header $(SKETCH)/gamedata.h

$(FXDIR)/fxdata.h: $(FXDIR)/fxdata.txt $(FXDIR)/game.bin | $(PYTHON)
	$(PYTHON) tools/vendor/fxdata-build.py $(FXDIR)/fxdata.txt >/dev/null

$(BUILD)/$(SKETCH).ino.hex: $(FXDIR)/fxdata.h $(SKETCH)/gamedata.h $(SOURCES)
	$(ARDUINO_CLI) compile --fqbn $(FQBN) --warnings all --output-dir $(BUILD) $(SKETCH)

package: $(PACKAGE)

$(PACKAGE): $(BUILD)/$(SKETCH).ino.hex $(FXDIR)/fxdata.h tools/package.py | $(PYTHON)
	$(PYTHON) tools/package.py --hex $(BUILD)/$(SKETCH).ino.hex --data $(FXDIR)/fxdata-data.bin \
	  --cart $(BUILD)/preview/title.png --game $(FXDIR)/game.bin --out $@

# Host-Prüfstand: die Engine-Quellen gegen eine Nachbildung von Arduboy2 und
# ArduboyFX übersetzt (tests/fxhost). Ui.cpp bindet harness.cpp selbst ein.
# Die Schrift kommt aus der Arduboy2-Bibliothek, die build.sh einrichtet.
FXHOST      := $(BUILD)/fxhost
FXHOST_SRC  := $(SKETCH)/World.cpp $(SKETCH)/Script.cpp tests/fxhost/harness.cpp
FXHOST_FONT := $(BUILD)/fxhost_font.cpp
ARDUBOY2_DATA := $(firstword $(shell find $(BUILD)/arduino $(HOME)/Library/Arduino15 $(HOME)/.arduino15 \
                   -path '*Arduboy2/src/Arduboy2Data.cpp' 2>/dev/null))
HOSTCXX     ?= c++
HOSTFLAGS   := -std=c++17 -O1 -g -Wall -Wno-unused-function -Wno-pragma-pack -include tests/fxhost/prelude.h -Itests/fxhost/shim

fxhost: $(FXHOST)

$(FXHOST_FONT):
	@test -n "$(ARDUBOY2_DATA)" || (echo "Arduboy2 library not found: run ./build.sh first"; exit 1)
	{ echo '#include <cstdint>'; echo 'namespace host { extern const uint8_t font5x7[] = {'; \
	  sed -n '/font5x7\[\] = {/,/^};/p' "$(ARDUBOY2_DATA)" | sed '1d;$$d'; echo '}; }'; } > $@

$(FXHOST): $(FXHOST_SRC) $(FXHOST_FONT) $(SOURCES) $(wildcard tests/fxhost/*.h tests/fxhost/shim/*.h) $(FXDIR)/fxdata.h $(SKETCH)/gamedata.h
	$(HOSTCXX) $(HOSTFLAGS) -o $@ $(FXHOST_SRC) -x c++ $(SKETCH)/$(SKETCH).ino -x none $(FXHOST_FONT) -lz

art: | $(PYTHON)
	$(PYTHON) tools/draw_art.py

test: $(FXHOST) | $(PYTHON)
	$(PYTHON) -m unittest discover -s tests -v

upload: upload-sketch upload-data

upload-sketch: $(BUILD)/$(SKETCH).ino.hex
	@test -n "$(PORT)" || (echo "No Arduboy found (/dev/cu.usbmodem*). Is it switched on?"; exit 1)
	$(ARDUINO_CLI) upload --fqbn $(FQBN) --port $(PORT) --input-dir $(BUILD) $(SKETCH)

upload-data: $(FXDIR)/fxdata.h
	$(PYTHON) tools/vendor/fxdata-upload.py $(FXDIR)/fxdata.bin

clean:
	rm -rf $(BUILD) dist $(FXDIR)/game.bin $(FXDIR)/fxdata.h $(FXDIR)/fxdata.bin $(FXDIR)/fxdata-data.bin $(SKETCH)/gamedata.h
